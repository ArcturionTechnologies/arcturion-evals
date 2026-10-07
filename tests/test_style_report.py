import contextlib
import io
import json
import unittest
from helpers import TempCase
from arcturion_evals import style_report as report

DIMS = {"brevity": 9, "plain_english": 8, "summary_format": 7, "decisiveness": 8}


class TestStyleReport(TempCase):
    def history(self, records):
        folder = self.root / "history"
        folder.mkdir(exist_ok=True)
        with open(folder / "2026-08-04_synthetic.jsonl", "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return folder

    def rec(self, model, brevity=8.0, plain=8.0, summary=8.0, decisive=8.0, **extra):
        row = {"ts": "2026-08-04T02:00:00", "suite": "s", "task_id": "x", "final": 7.0, "dry_run": False,
               "model": model, "style": {"brevity": brevity, "plain_english": plain,
                                         "summary_format": summary, "decisiveness": decisive}}
        row.update(extra)
        return row

    def test_rows_without_complete_style_are_skipped_not_counted(self):
        folder = self.history([
            {"ts": "2026-07-21T10:00:00", "suite": "s", "task_id": "a1", "final": 7.5, "dry_run": False},
            self.rec("m1"),
            {"ts": "2026-08-01T10:00:00", "suite": "s", "task_id": "a3", "final": 8.0, "dry_run": False,
             "style": {"brevity": 9, "plain_english": 8, "summary_format": 7}},  # one dimension missing
            self.rec("m2", dry_run=True),
        ])
        rows = report.load_style_rows(folder)
        self.assertEqual([r["model"] for r in rows], ["m1"])

    def test_legacy_agent_key_still_groups(self):
        folder = self.history([{"ts": "2026-08-01T10:00:00", "agent": "older", "task_id": "a", "final": 5, "dry_run": False,
                                "style": DIMS}])
        self.assertEqual(report.load_style_rows(folder)[0]["suite"], "older")

    def test_weak_model_flagged_with_enough_samples(self):
        folder = self.history([self.rec("weak", brevity=3.0) for _ in range(10)])
        means = report.group_means(report.load_style_rows(folder), "model")
        self.assertEqual(report.find_candidates(means, 7.0, 10), [("weak", "brevity", 3.0, 10)])

    def test_too_few_samples_not_flagged(self):
        folder = self.history([self.rec("weak", brevity=2.0) for _ in range(9)])
        means = report.group_means(report.load_style_rows(folder), "model")
        self.assertEqual(report.find_candidates(means, 7.0, 10), [])

    def test_decisiveness_participates(self):
        folder = self.history([self.rec("menu", decisive=2.0) for _ in range(10)])
        means = report.group_means(report.load_style_rows(folder), "model")
        self.assertEqual(report.find_candidates(means, 7.0, 10), [("menu", "decisiveness", 2.0, 10)])

    def test_strong_model_not_flagged(self):
        folder = self.history([self.rec("good", 9.0, 9.0, 9.0, 9.0) for _ in range(12)])
        means = report.group_means(report.load_style_rows(folder), "model")
        self.assertEqual(report.find_candidates(means, 7.0, 10), [])

    def test_unlabeled_models_group_together(self):
        folder = self.history([self.rec(None), self.rec("")])
        self.assertEqual({r["model"] for r in report.load_style_rows(folder)}, {"unlabeled"})

    def test_report_always_returns_zero_and_labels_candidates(self):
        folder = self.history([self.rec("weak", 1.0, 1.0, 1.0, 1.0) for _ in range(15)])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = report.run_report(folder)
        self.assertEqual(rc, 0)
        self.assertIn("REVIEW CANDIDATE", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(report.run_report(self.root / "no-history"), 0)
        self.assertIn("Nothing to report", out.getvalue())


if __name__ == "__main__":
    unittest.main()
