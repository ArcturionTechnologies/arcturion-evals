import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from helpers import TempCase, under
from arcturion_evals import runner


class TestRotation(TempCase):
    def test_rotation_wraps_and_covers_all(self):
        cfg = self.config()
        flat = [("A", {"id": "a-%03d" % i}) for i in range(7)]
        seen = []
        for _ in range(3):  # 3 runs x slice 3 over 7 tasks wraps
            seen += [t["id"] for _, t in runner.pick_slice(flat, 3, cfg)]
        self.assertEqual(len(seen), 9)
        self.assertEqual(set(seen[:7]), {"a-%03d" % i for i in range(7)})
        self.assertEqual(json.loads(cfg.rotation_file.read_text())["next_index"], 9 % 7)

    def test_empty_list(self):
        self.assertEqual(runner.pick_slice([], 5, self.config()), [])

    def test_no_advance_writes_nothing(self):
        cfg = self.config()
        picked = runner.pick_slice([("A", {"id": "t1"})], 5, cfg, advance=False)
        self.assertEqual([t["id"] for _, t in picked], ["t1"])
        self.assertFalse(cfg.rotation_file.exists())
        self.assertFalse(self.state.exists())

    def test_corrupt_pointer_restarts_at_zero(self):
        cfg = self.config()
        self.state.mkdir()
        cfg.rotation_file.write_text("{not json")
        picked = runner.pick_slice([("A", {"id": "t1"}), ("A", {"id": "t2"})], 1, cfg)
        self.assertEqual(picked[0][1]["id"], "t1")


class TestScoring(TempCase):
    def test_exec_error_scores_zero(self):
        rec = runner.score_task(self.config(), "S", {"id": "t", "checks": [], "rubric": "r"}, "", "timeout")
        self.assertEqual(rec["final"], 0.0)
        self.assertEqual(rec["exec_error"], "timeout")

    def test_deterministic_only_without_a_judge(self):
        task = {"id": "t", "checks": [{"type": "contains", "value": "ok"}], "rubric": "quality"}
        rec = runner.score_task(self.config(), "S", task, "all ok", None)
        self.assertEqual(rec["final"], 10.0)
        self.assertIsNone(rec["judge_score"])
        self.assertIsNone(rec["judge_error"])

    def test_dry_run_skips_judge(self):
        class Judge:
            def judge(self, *a):
                raise AssertionError("dry run must not judge")
        task = {"id": "t", "checks": [{"type": "contains", "value": "ok"}], "rubric": "quality"}
        rec = runner.score_task(self.config(judge=Judge()), "S", task, "ok", None, dry_run=True)
        self.assertEqual(rec["final"], 10.0)

    def test_final_is_mean_of_deterministic_and_judge(self):
        class Judge:
            def judge(self, *a):
                return {"score": 6.0, "rationale": "fine", "style": {"brevity": 9, "plain_english": 8,
                        "summary_format": 7, "decisiveness": 6}, "error": None}
        task = {"id": "t", "prompt": "p", "checks": [{"type": "contains", "value": "ok"}], "rubric": "r", "model": "m1"}
        rec = runner.score_task(self.config(judge=Judge()), "S", task, "ok", None)
        self.assertEqual(rec["final"], 8.0)
        self.assertEqual(rec["style"]["brevity"], 9)
        self.assertEqual(rec["model"], "m1")
        self.assertEqual(json.loads(json.dumps(rec))["style"]["decisiveness"], 6)

    def test_judge_failure_falls_back_to_deterministic_and_is_recorded(self):
        class Judge:
            def judge(self, *a):
                return {"score": None, "rationale": "", "style": None, "error": "judge timeout after 180s"}
        task = {"id": "t", "prompt": "p", "checks": [{"type": "contains", "value": "ok"}], "rubric": "r"}
        rec = runner.score_task(self.config(judge=Judge()), "S", task, "ok", None)
        self.assertEqual(rec["final"], 10.0)
        self.assertEqual(rec["judge_error"], "judge timeout after 180s")
        self.assertIsNone(rec["style"])

    def test_source_defaults_to_suite(self):
        cfg = self.config()
        self.assertEqual(runner.score_task(cfg, "S", {"id": "a", "checks": []}, "o", None, dry_run=True)["source"], "suite")
        self.assertEqual(runner.score_task(cfg, "S", {"id": "a", "source": "intake", "checks": []}, "o", None,
                                           dry_run=True)["source"], "intake")


class TestExecution(TempCase):
    def test_prompt_on_stdin_and_task_env_passed(self):
        argv = self.agent_script("""
            import os, sys
            prompt = sys.stdin.read()
            print("%s|%s|%s|%s" % (prompt, os.environ["EVALS_TASK_ID"], os.environ["EVALS_SUITE"], os.environ["EVALS_MODEL"]))
        """)
        cfg = self.config(agent_cmd=argv)
        out, err = runner.execute_task(cfg, "demo", {"id": "t9", "prompt": "hello there", "model": "m-label"})
        self.assertIsNone(err)
        self.assertEqual(out.strip(), "hello there|t9|demo|m-label")

    def test_workdir_is_honored(self):
        work = self.root / "work"
        work.mkdir()
        argv = self.agent_script("import os; print(os.path.realpath(os.getcwd()))")
        out, err = runner.execute_task(self.config(agent_cmd=argv, workdir=work), "s", {"id": "t", "prompt": "p"})
        self.assertIsNone(err)
        self.assertEqual(out.strip(), os.path.realpath(work))  # /var vs /private/var safe

    def test_missing_workdir_and_missing_command_are_errors_not_crashes(self):
        argv = self.agent_script("print('x')")
        _, err = runner.execute_task(self.config(agent_cmd=argv, workdir=self.root / "nope"), "s", {"id": "t", "prompt": "p"})
        self.assertIn("workdir does not exist", err)
        _, err = runner.execute_task(self.config(), "s", {"id": "t", "prompt": "p"})
        self.assertIn("no agent command configured", err)
        _, err = runner.execute_task(self.config(agent_cmd=[str(self.root / "missing-binary")]), "s", {"id": "t", "prompt": "p"})
        self.assertIn("failed to start", err)

    def test_timeout_and_nonzero_exit(self):
        slow = self.agent_script("import time; time.sleep(5)")
        _, err = runner.execute_task(self.config(agent_cmd=slow), "s", {"id": "t", "prompt": "p", "timeout_s": 1})
        self.assertEqual(err, "timeout after 1s")
        bad = self.agent_script("import sys; sys.stderr.write('boom'); sys.exit(4)")
        out, err = runner.execute_task(self.config(agent_cmd=bad), "s", {"id": "t", "prompt": "p"})
        self.assertIn("rc=4", err)
        self.assertIn("boom", err)

    def test_malformed_timeout_falls_back_to_default(self):
        argv = self.agent_script("print('fine')")
        out, err = runner.execute_task(self.config(agent_cmd=argv), "s", {"id": "t", "prompt": "p", "timeout_s": "soon"})
        self.assertIsNone(err)


class TestSuiteLoading(TempCase):
    def test_invalid_suite_skipped_valid_survive(self):
        (self.suites / "BAD.json").write_text("{not json")
        (self.suites / "WRONG.json").write_text(json.dumps({"suite": "w", "tasks": "nope"}))
        self.write_suite("good")
        messages = []
        flat = runner.load_suites(self.config(), log=messages.append)
        self.assertEqual([(s, t["id"]) for s, t in flat], [("good", "t1"), ("good", "t2")])
        self.assertEqual(sum("SUITE SKIPPED" in m for m in messages), 2)

    def test_legacy_agent_key_is_accepted(self):
        self.write_suite("legacy", key="agent")
        self.assertEqual({s for s, _ in runner.load_suites(self.config())}, {"legacy"})

    def test_missing_suites_dir_is_empty(self):
        self.assertEqual(runner.load_suites(self.config(suites_dir=self.root / "absent")), [])

    def test_extra_cases_are_appended_after_suites(self):
        self.write_suite("good")
        extra = self.root / "cases.ndjson"
        extra.write_text(json.dumps({
            "id": "case-1", "agent": "assistant", "kind": "behavior",
            "prompt": "A scheduled job reports success but the output file is empty and nobody has checked it.",
            "expected_behavior": "Verify the output before reporting success."}) + "\n")
        flat = runner.load_suites(self.config(extra_cases=extra), log=lambda m: None)
        self.assertEqual([t["id"] for _, t in flat], ["t1", "t2", "case-1"])
        self.assertEqual(flat[-1][1]["source"], "intake")


class TestRun(TempCase):
    def setUp(self):
        super().setUp()
        self.write_suite("demo")
        self.argv = self.agent_script("import sys; sys.stdin.read(); print('x present')")
        self.logs = []

    def run_cmd(self, cfg, **kw):
        with patch.object(runner, "log", self.logs.append):
            return runner.cmd_run(cfg, **kw)

    def test_dry_run_touches_no_state(self):
        cfg = self.config(agent_cmd=self.argv)
        with patch.object(runner.dash, "generate", side_effect=AssertionError("no dashboard on dry run")):
            rc = self.run_cmd(cfg, slice_size=1, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertFalse(self.state.exists())  # not even the state folder is created

    def test_real_run_writes_state_only_inside_the_configured_state_dir(self):
        """The regression this repo exists to prevent: a run must never write outside state_dir."""
        fake_home = self.root / "home"
        fake_home.mkdir()
        cfg = self.config(agent_cmd=self.argv)
        with patch.dict(os.environ, {"HOME": str(fake_home), "XDG_STATE_HOME": str(fake_home / "xdg")}):
            rc = self.run_cmd(cfg, slice_size=1)
        self.assertEqual(rc, 0)
        self.assertEqual(list(fake_home.iterdir()), [])  # nothing leaked into HOME
        self.assertEqual(json.loads(cfg.rotation_file.read_text())["next_index"], 1)
        history = list(cfg.history_dir.glob("*.jsonl"))
        self.assertEqual(len(history), 1)
        receipt = json.loads(cfg.receipt_file.read_text())
        self.assertEqual(receipt["status"], "succeeded")
        self.assertTrue(under(receipt["artifact"], self.state))
        self.assertTrue(cfg.dashboard_path.is_file())
        written = {Path(root, name) for root, _, names in os.walk(self.root) for name in names}
        self.assertTrue(all(under(p, self.root) for p in written))

    def test_selecting_one_suite_or_task(self):
        cfg = self.config(agent_cmd=self.argv)
        self.assertEqual(self.run_cmd(cfg, task_id="t2"), 0)
        rows = [json.loads(line) for f in cfg.history_dir.glob("*.jsonl") for line in f.read_text().splitlines()]
        self.assertEqual([r["task_id"] for r in rows], ["t2"])
        self.assertEqual(self.run_cmd(cfg, task_id="nope"), 1)
        self.assertEqual(json.loads(cfg.receipt_file.read_text())["reason"], "selection matched no tasks")

    def test_no_suites_is_a_failed_receipt_in_state_dir(self):
        cfg = self.config(suites_dir=self.root / "absent", agent_cmd=self.argv)
        self.assertEqual(self.run_cmd(cfg), 1)
        self.assertEqual(json.loads(cfg.receipt_file.read_text())["status"], "failed")

    def test_systematic_failure_quarantines_history(self):
        cfg = self.config()  # no agent command: every task errors
        self.assertEqual(self.run_cmd(cfg, slice_size=2), 1)
        self.assertEqual(list(cfg.history_dir.glob("*.jsonl")), [])
        self.assertEqual(len(list(cfg.history_dir.glob("*.failed"))), 1)
        self.assertEqual(json.loads(cfg.receipt_file.read_text())["status"], "failed")

    def test_failure_is_passed_to_the_notify_command_when_configured(self):
        sink = self.root / "notified.txt"
        notify = self.agent_script("import sys; open(%r, 'w').write(sys.stdin.read())" % str(sink))
        cfg = self.config(notify_cmd=notify)
        self.run_cmd(cfg, slice_size=1)
        self.assertIn("run failed", sink.read_text())


if __name__ == "__main__":
    unittest.main()
