"""Integrity of the bundled synthetic suites and the replay agent that exercises them."""
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch
from helpers import ROOT, TempCase
from arcturion_evals import checks, runner

SUITES = sorted((ROOT / "suites").glob("*.json"))
ANSWERS = json.loads((ROOT / "examples" / "replay_answers.json").read_text())
CHECK_TYPES = {"regex", "not_regex", "contains", "not_contains", "number_close", "json_field",
               "max_words", "min_words"}


def all_tasks():
    for path in SUITES:
        suite = json.loads(path.read_text())
        for task in suite["tasks"]:
            yield suite["suite"], task


class TestBundledSuites(unittest.TestCase):
    def test_there_are_suites_and_they_are_well_formed(self):
        self.assertGreaterEqual(len(SUITES), 6)
        for path in SUITES:
            suite = json.loads(path.read_text())
            self.assertEqual(path.stem, suite["suite"])
            self.assertTrue(suite["description"])
            self.assertGreaterEqual(len(suite["tasks"]), 8, path.name)

    def test_task_ids_are_unique_and_prefixed_by_suite(self):
        ids = [t["id"] for _, t in all_tasks()]
        self.assertEqual(len(ids), len(set(ids)))
        for suite, task in all_tasks():
            self.assertRegex(task["id"], r"^[a-z]+-\d{3}$", task["id"])

    def test_every_task_is_self_contained_and_checkable(self):
        for _, task in all_tasks():
            with self.subTest(task=task["id"]):
                self.assertTrue(task["prompt"].strip())
                self.assertTrue(task["title"].strip())
                self.assertTrue(task["rubric"].strip())
                self.assertGreater(task["weight"], 0)
                self.assertTrue(task["checks"], "every task needs at least one deterministic check")
                for spec in task["checks"]:
                    self.assertIn(spec["type"], CHECK_TYPES)
                    if "pattern" in spec:
                        re.compile(spec["pattern"])
                # nothing may depend on files, tools or live state
                self.assertNotIn("allowed_tools", task)
                self.assertNotIn("/", task.get("workdir", ""))

    def test_content_is_generic(self):
        """The suites must not quiz on credentials, people, paths or any private setup."""
        banned = re.compile(r"(?i)password|passphrase|api[ _-]?key|token|secret|credential|private key|"
                            r"/users/|~/|\.env\b|@[a-z0-9-]+\.[a-z]{2,}|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b")
        for path in SUITES:
            text = path.read_text()
            with self.subTest(suite=path.name):
                self.assertIsNone(banned.search(text))

    def test_replay_answers_cover_every_task_and_nothing_else(self):
        self.assertEqual(set(ANSWERS), {t["id"] for _, t in all_tasks()})

    def test_replay_answers_pass_every_check(self):
        for _, task in all_tasks():
            with self.subTest(task=task["id"]):
                self.assertEqual(checks.deterministic_score(ANSWERS[task["id"]], task["checks"]), 10.0)

    def test_empty_and_wrong_answers_do_not_pass(self):
        scores = [checks.deterministic_score(wrong, task["checks"])
                  for _, task in all_tasks() for wrong in ("", "I don't know.")]
        self.assertLess(sum(scores) / len(scores), 3.0)
        for _, task in all_tasks():
            with self.subTest(task=task["id"]):
                self.assertLess(checks.deterministic_score("", task["checks"]), 10.0)


class TestReplayAgentEndToEnd(TempCase):
    def test_full_run_over_the_bundled_suites_with_the_replay_agent(self):
        agent = [sys.executable, str(ROOT / "examples" / "replay_agent.py")]
        cfg = self.config(suites_dir=ROOT / "suites", agent_cmd=agent)
        with patch.object(runner, "log", lambda msg: None):
            self.assertEqual(runner.cmd_run(cfg, slice_size=1000), 0)
        rows = [json.loads(line) for f in cfg.history_dir.glob("*.jsonl") for line in f.read_text().splitlines()]
        self.assertEqual(len(rows), len(ANSWERS))
        self.assertTrue(all(r["final"] == 10.0 and r["exec_error"] is None for r in rows))

    def test_replay_agent_fails_loudly_for_an_unknown_task(self):
        proc = subprocess.run([sys.executable, str(ROOT / "examples" / "replay_agent.py")], input="p",
                              capture_output=True, text=True, env={"EVALS_TASK_ID": "nope"})
        self.assertEqual(proc.returncode, 3)


if __name__ == "__main__":
    unittest.main()
