#!/usr/bin/env python3
"""Run golden-task suites against any agent command and score them. Stdlib only.

The agent under test is just a command (EVALS_AGENT_CMD). Each task's prompt arrives on its
stdin and the answer is read from stdout, so any agent, script or model wrapper can be graded.
Tasks run with the working directory set by EVALS_WORKDIR (default: the current directory) and
these environment variables added: EVALS_TASK_ID, EVALS_SUITE, EVALS_MODEL.
"""
import datetime as dt
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

from . import checks as checks_mod
from . import dashboard as dash
from . import intake
from . import judge as judge_mod
from .config import DEFAULT_TIMEOUT_S


def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def _atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp, path)  # atomic: a crash mid-write never truncates the file


def write_receipt(cfg, status, **fields):
    """Durable local record of the last run, under the state folder only. It never claims delivery."""
    payload = {"at": dt.datetime.now().isoformat(timespec="seconds"), "status": status, **fields}
    _atomic_write_json(cfg.receipt_file, payload)
    if status == "failed" and cfg.notify_cmd:
        dash.notify(cfg, "ArcturionEvals run failed: %s" % (fields.get("reason") or "every task errored"))
    return payload


def load_suites(cfg, log=log):
    """Return flattened, stably ordered [(suite, task), ...]. Bad suites are skipped loudly."""
    flat = []
    suites_dir = Path(cfg.suites_dir)
    if suites_dir.is_dir():
        for path in sorted(suites_dir.glob("*.json")):
            try:
                suite = json.loads(path.read_text())
                name = suite.get("suite") or suite["agent"]
                tasks = suite["tasks"]
                if not isinstance(name, str) or not isinstance(tasks, list) or not all(isinstance(t, dict) for t in tasks):
                    raise KeyError("suite name must be a string and tasks a list of objects")
            except (json.JSONDecodeError, KeyError, AttributeError, OSError) as exc:
                log("SUITE SKIPPED (invalid): %s - %s" % (path.name, exc))
                continue
            for task in sorted(tasks, key=lambda t: t.get("id", "")):
                flat.append((name, task))
    if cfg.extra_cases:
        flat.extend(intake.load_cases(str(cfg.extra_cases), log=log))
    return flat


def pick_slice(flat, slice_size, cfg, advance=True):
    """Rotate a pointer over the flattened list; wraps so every task runs in turn.

    advance=False (dry run) reads the pointer but never writes it back, so a preview
    cannot consume the slot the real run needs.
    """
    pointer = 0
    try:
        pointer = int(json.loads(Path(cfg.rotation_file).read_text()).get("next_index", 0))
    except (OSError, ValueError, AttributeError):
        pass
    n = len(flat)
    if n == 0:
        return []
    pointer %= n
    picked = [flat[(pointer + i) % n] for i in range(min(slice_size, n))]
    if advance:
        _atomic_write_json(cfg.rotation_file, {
            "next_index": (pointer + len(picked)) % n, "total_tasks": n,
            "updated": dt.datetime.now().isoformat(timespec="seconds")})
    return picked


def _num(value, default, cast):
    """Coerce a task field to a number; malformed suite values fall back to default."""
    try:
        return cast(value)
    except (TypeError, ValueError):
        return cast(default)


def execute_task(cfg, suite, task, dry_run=False):
    """Run one task through the agent command. Returns (output, error)."""
    if dry_run:
        return ("[dry-run] no execution", None)
    if not cfg.agent_cmd:
        return ("", "no agent command configured (set EVALS_AGENT_CMD or pass --agent-cmd)")
    workdir = str(cfg.workdir) if cfg.workdir else None
    if workdir and not os.path.isdir(workdir):
        return ("", "workdir does not exist: %s" % workdir)
    env = dict(cfg.environ)
    env.update(EVALS_TASK_ID=str(task.get("id", "")), EVALS_SUITE=suite, EVALS_MODEL=str(task.get("model", "")))
    timeout = _num(task.get("timeout_s"), DEFAULT_TIMEOUT_S, int)
    try:
        proc = subprocess.run(cfg.agent_cmd, input=task["prompt"], cwd=workdir, env=env,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ("", "timeout after %ss" % timeout)
    except OSError as exc:
        return ("", "agent command failed to start: %s" % exc)
    if proc.returncode != 0:
        return (proc.stdout or "", "agent rc=%s: %s" % (proc.returncode, (proc.stderr or "")[-300:]))
    return (proc.stdout or "", None)


def score_task(cfg, suite, task, output, exec_error, dry_run=False):
    judged = {"score": None, "rationale": "", "style": None, "error": None}
    if exec_error is not None:
        det = None  # checks weren't meaningfully run against a failed execution
        final = 0.0  # a crashed or timed-out agent IS a regression signal
    else:
        det = checks_mod.deterministic_score(output, task.get("checks", []))
        if task.get("rubric") and not dry_run:
            judged = judge_mod.judge_output(cfg.judge, task.get("prompt", ""), task["rubric"], output)
        scores = [s for s in (det, judged["score"]) if s is not None]
        final = round(sum(scores) / len(scores), 2) if scores else 0.0
    return {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "suite": suite,
        "task_id": task.get("id", "?"),
        "title": task.get("title", ""),
        "model": task.get("model", ""),
        "det_score": det,
        "judge_score": judged["score"],
        "judge_rationale": judged["rationale"],
        "judge_error": judged["error"],
        "style": judged["style"],
        "exec_error": exec_error,
        "final": final,
        "weight": _num(task.get("weight"), 1.0, float),
        "source": task.get("source", "suite"),
    }


def cmd_run(cfg, slice_size=15, dry_run=False, suite=None, task_id=None):
    flat = load_suites(cfg)
    if not flat:
        log("no suites found in %s, nothing to run" % cfg.suites_dir)
        write_receipt(cfg, "failed", reason="no suites found")
        return 1
    if suite or task_id:
        picked = [(s, t) for s, t in flat
                  if (not suite or s == suite) and (not task_id or t.get("id") == task_id)]
    else:
        picked = pick_slice(flat, slice_size, cfg, advance=not dry_run)
    if not picked:
        log("selection matched no tasks")
        write_receipt(cfg, "failed", reason="selection matched no tasks")
        return 1
    run_id = uuid.uuid4().hex[:8]
    log("run %s: %d task(s): %s" % (run_id, len(picked), ", ".join("%s/%s" % (s, t.get("id")) for s, t in picked)))
    if dry_run:
        # A dry run writes NOTHING to real state: its rows go to a throwaway temp folder.
        history_dir = Path(tempfile.mkdtemp(prefix="arcturion_evals_dryrun_"))
    else:
        history_dir = cfg.history_dir
        history_dir.mkdir(parents=True, exist_ok=True)
    out_path = history_dir / ("%s_%s.jsonl" % (dt.date.today().isoformat(), run_id))
    results = []
    for name, task in picked:
        log("-> %s / %s" % (name, task.get("id")))
        output, exec_error = execute_task(cfg, name, task, dry_run=dry_run)
        rec = score_task(cfg, name, task, output, exec_error, dry_run=dry_run)
        rec["run_id"], rec["dry_run"] = run_id, bool(dry_run)
        results.append(rec)
        with open(out_path, "a") as f:  # append per task: a crash never loses prior results
            f.write(json.dumps(rec) + "\n")
        log("   final=%.1f det=%s judge=%s%s" % (rec["final"], rec["det_score"], rec["judge_score"],
                                               (" ERROR: " + exec_error) if exec_error else ""))
    mean = sum(r["final"] for r in results) / len(results)
    log("run %s done: mean %.2f over %d task(s) -> %s" % (run_id, mean, len(results), out_path))
    if not dry_run and all(r["exec_error"] for r in results):
        os.replace(out_path, str(out_path) + ".failed")  # quarantine so it never poisons trend history
        log("SYSTEMATIC FAILURE: every task errored (is the agent command right?); history quarantined to %s.failed" % out_path)
        write_receipt(cfg, "failed", run_id=run_id, tasks=len(results), errors=len(results), artifact=str(out_path) + ".failed")
        return 1
    if dry_run:
        log("dry-run: rotation, history, receipt and dashboard untouched; preview rows at %s" % out_path)
        return 0
    write_receipt(cfg, "succeeded", run_id=run_id, tasks=len(results),
                  errors=sum(bool(r["exec_error"]) for r in results), artifact=str(out_path))
    dash.generate(cfg)
    if cfg.notify_cmd and dt.date.today().weekday() == 6:  # Sunday: weekly digest
        dash.digest(cfg, send=True)
    return 0
