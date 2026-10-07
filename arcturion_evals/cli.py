"""Command line: run, list, dashboard, digest, report."""
import argparse
import sys

from . import dashboard as dash
from . import runner
from . import style_report
from .config import Config, DEFAULT_SLICE


def build_parser():
    ap = argparse.ArgumentParser(prog="arcturion-evals", description="ArcturionEvals: golden-task eval harness")
    ap.add_argument("--suites-dir", help="folder of suite JSON files (env EVALS_SUITES_DIR, default ./suites)")
    ap.add_argument("--state-dir", help="history/rotation/receipt folder (env EVALS_STATE_DIR, default ./evals-state)")
    ap.add_argument("--workdir", help="working directory for the agent command (env EVALS_WORKDIR)")
    ap.add_argument("--agent-cmd", help="command that runs the agent; prompt on stdin, answer on stdout (env EVALS_AGENT_CMD)")
    ap.add_argument("--extra-cases", help="optional JSONL of extra cases (env EVALS_EXTRA_CASES)")
    ap.add_argument("--notify-cmd", help="command that receives the digest line on stdin (env EVALS_NOTIFY_CMD)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a rotating slice, one suite, or one task")
    r.add_argument("--slice", type=int, default=DEFAULT_SLICE)
    r.add_argument("--dry-run", action="store_true", help="no execution and nothing written to state")
    r.add_argument("--suite")
    r.add_argument("--task")
    sub.add_parser("list", help="list the tasks that would run, in rotation order")
    sub.add_parser("dashboard", help="write the HTML trend dashboard")
    d = sub.add_parser("digest", help="print the weekly line")
    d.add_argument("--send", action="store_true", help="also pass it to the notify command")
    p = sub.add_parser("report", help="response-style report from judged history")
    p.add_argument("--threshold", type=float, default=style_report.DEFAULT_THRESHOLD)
    p.add_argument("--min-samples", type=int, default=style_report.DEFAULT_MIN_SAMPLES)
    return ap


def main(argv=None, environ=None):
    args = build_parser().parse_args(argv)
    cfg = Config.from_env(environ, suites_dir=args.suites_dir, state_dir=args.state_dir, workdir=args.workdir,
                          agent_cmd=args.agent_cmd, extra_cases=args.extra_cases, notify_cmd=args.notify_cmd)
    if args.cmd == "run":
        return runner.cmd_run(cfg, args.slice, args.dry_run, args.suite, args.task)
    if args.cmd == "list":
        for name, task in runner.load_suites(cfg):
            print("%s/%s\t%s" % (name, task.get("id"), task.get("title", "")))
        return 0
    if args.cmd == "dashboard":
        print(dash.generate(cfg))
        return 0
    if args.cmd == "digest":
        print(dash.digest(cfg, send=args.send))
        return 0
    return style_report.run_report(cfg.history_dir, args.threshold, args.min_samples)


if __name__ == "__main__":
    sys.exit(main())
