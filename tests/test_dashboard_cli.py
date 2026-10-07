import contextlib
import datetime as dt
import io
import json
import os
import sys
import unittest
from unittest.mock import patch
from helpers import TempCase, ROOT, under
from arcturion_evals import cli, dashboard as dash
from arcturion_evals.config import Config


def row(day, suite, final, task="t", **extra):
    base = {"ts": day + "T10:00:00", "suite": suite, "task_id": task, "final": final, "dry_run": False}
    base.update(extra)
    return base


class TestDashboard(TempCase):
    def history(self, records):
        folder = self.state / "history"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "2026-07-21_a.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records) + "garbage line\n")

    def test_history_skips_dry_runs_and_garbage_and_accepts_legacy_agent_key(self):
        self.history([row("2026-07-21", "s", 7.5), row("2026-07-21", "s", 1.0, dry_run=True),
                      {"ts": "2026-07-21T10:00:00", "agent": "old", "task_id": "a", "final": 6.0}])
        rows = dash.load_history(self.state / "history")
        self.assertEqual([(r["suite"], r["final"]) for r in rows], [("s", 7.5), ("old", 6.0)])

    def test_generate_writes_inside_state_and_escapes_names(self):
        self.history([row("2026-07-21", "<b>x</b>", 7.0), row("2026-07-22", "<b>x</b>", 9.0)])
        path = dash.generate(self.config())
        self.assertTrue(under(path, self.state))
        page = path.read_text()
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("9.0", page)

    def test_digest_compares_weeks_and_names_the_weakest_task(self):
        today = dt.date(2026, 8, 10)
        self.history([row("2026-08-09", "alpha", 9.0, "a1"), row("2026-08-08", "alpha", 3.0, "a2"),
                      row("2026-08-01", "alpha", 9.5, "a3"), row("2026-08-09", "beta", 8.0, "b1")])
        line = dash.digest(self.config(), today=today)
        self.assertIn("overall 6.7/10", line)
        self.assertIn("alpha 6.0 down", line)
        self.assertIn("beta 8.0 new", line)
        self.assertIn("weakest: alpha/a2 (3.0)", line)

    def test_digest_with_nothing_this_week(self):
        self.assertIn("no eval runs", dash.digest(self.config(), today=dt.date(2026, 8, 10)))

    def test_notify_pipes_the_line_to_the_command(self):
        sink = self.root / "sink.txt"
        argv = self.agent_script("import sys; open(%r, 'w').write(sys.stdin.read())" % str(sink))
        self.assertTrue(dash.notify(self.config(notify_cmd=argv), "hello digest"))
        self.assertEqual(sink.read_text(), "hello digest")

    def test_notify_without_a_command_or_with_a_failing_one_does_not_raise(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(dash.notify(self.config(), "x"))
            self.assertFalse(dash.notify(self.config(notify_cmd=self.agent_script("import sys; sys.exit(5)")), "x"))
            self.assertFalse(dash.notify(self.config(notify_cmd=[str(self.root / "missing")]), "x"))


class TestConfig(TempCase):
    def test_environment_and_override_precedence(self):
        env = {"EVALS_SUITES_DIR": "/env/suites", "EVALS_STATE_DIR": "/env/state", "EVALS_AGENT_CMD": "my-agent --fast",
               "EVALS_NOTIFY_CMD": "notify-me", "EVALS_WORKDIR": "/env/work", "EVALS_EXTRA_CASES": "/env/cases.ndjson"}
        cfg = Config.from_env(env)
        self.assertEqual((str(cfg.suites_dir), str(cfg.state_dir), str(cfg.workdir)), ("/env/suites", "/env/state", "/env/work"))
        self.assertEqual(cfg.agent_cmd, ["my-agent", "--fast"])
        self.assertEqual(cfg.notify_cmd, ["notify-me"])
        self.assertIsNone(cfg.judge)
        cfg = Config.from_env(env, state_dir="/flag/state", agent_cmd="other-agent")
        self.assertEqual((str(cfg.state_dir), cfg.agent_cmd), ("/flag/state", ["other-agent"]))

    def test_defaults_are_relative_not_absolute_machine_paths(self):
        cfg = Config.from_env({})
        self.assertFalse(cfg.suites_dir.is_absolute())
        self.assertFalse(cfg.state_dir.is_absolute())
        self.assertIsNone(cfg.agent_cmd)

    def test_derived_paths_live_under_state_dir(self):
        cfg = self.config()
        for path in (cfg.history_dir, cfg.rotation_file, cfg.receipt_file, cfg.dashboard_path):
            self.assertTrue(under(path, self.state))


class TestCli(TempCase):
    def run_cli(self, *argv, env=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(argv), environ=env or {})
        return code, out.getvalue()

    def base(self):
        return ["--suites-dir", str(ROOT / "suites"), "--state-dir", str(self.state)]

    def test_list_shows_every_bundled_task(self):
        code, out = self.run_cli(*self.base(), "list")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 48)
        self.assertIn("arithmetic/arith-001", out)

    def test_dry_run_leaves_no_state(self):
        code, _ = self.run_cli(*self.base(), "run", "--dry-run", "--slice", "3")
        self.assertEqual(code, 0)
        self.assertFalse(self.state.exists())

    def test_run_then_dashboard_digest_and_report(self):
        agent = "%s %s" % (sys.executable, ROOT / "examples" / "replay_agent.py")
        code, _ = self.run_cli(*self.base(), "--agent-cmd", agent, "run", "--suite", "arithmetic")
        self.assertEqual(code, 0)
        code, out = self.run_cli(*self.base(), "dashboard")
        self.assertTrue(under(out.strip(), self.state))
        code, out = self.run_cli(*self.base(), "digest")
        self.assertIn("arithmetic 10.0 new", out)
        code, out = self.run_cli(*self.base(), "report")
        self.assertEqual(code, 0)
        self.assertIn("Nothing to report", out)

    def test_agent_command_and_state_can_come_from_the_environment(self):
        env = {"EVALS_AGENT_CMD": "%s %s" % (sys.executable, ROOT / "examples" / "replay_agent.py"),
               "EVALS_STATE_DIR": str(self.state), "EVALS_SUITES_DIR": str(ROOT / "suites")}
        code, _ = self.run_cli("run", "--task", "arith-004", env=env)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((self.state / "latest_run.json").read_text())["tasks"], 1)

    def test_run_without_an_agent_command_fails_clearly(self):
        code, _ = self.run_cli(*self.base(), "run", "--task", "arith-004")
        self.assertEqual(code, 1)
        self.assertEqual(len(list((self.state / "history").glob("*.failed"))), 1)


if __name__ == "__main__":
    unittest.main()
