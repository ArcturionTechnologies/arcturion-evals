import unittest
from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)
from arcturion_evals import checks


class TestChecks(unittest.TestCase):
    def test_regex_and_flags(self):
        self.assertTrue(checks.run_check("Report OK", {"type": "regex", "pattern": "report", "flags": "i"}))
        self.assertFalse(checks.run_check("Report OK", {"type": "regex", "pattern": "report"}))
        self.assertTrue(checks.run_check("Report OK", {"type": "not_regex", "pattern": "broken"}))

    def test_contains(self):
        self.assertTrue(checks.run_check("Hello World", {"type": "contains", "value": "hello", "flags": "i"}))
        self.assertFalse(checks.run_check("Hello World", {"type": "contains", "value": "hello"}))
        self.assertTrue(checks.run_check("clean", {"type": "not_contains", "value": "dirty"}))

    def test_number_close(self):
        out = "Total revenue was $1,234.50 across 3 invoices."
        self.assertTrue(checks.run_check(out, {"type": "number_close", "expect": 1234.5, "tol": 0.01}))
        self.assertFalse(checks.run_check(out, {"type": "number_close", "expect": 9999, "tol": 0.01}))

    def test_json_field(self):
        out = 'preamble {"a": {"b": 7}} trailing'
        self.assertTrue(checks.run_check(out, {"type": "json_field", "path": "a.b", "equals": 7}))
        self.assertFalse(checks.run_check(out, {"type": "json_field", "path": "a.c", "equals": 7}))
        self.assertFalse(checks.run_check("no json here", {"type": "json_field", "path": "a", "equals": 1}))

    def test_word_limits(self):
        self.assertTrue(checks.run_check("one two three", {"type": "max_words", "value": 3}))
        self.assertFalse(checks.run_check("one two three four", {"type": "max_words", "value": 3}))
        self.assertTrue(checks.run_check("one two", {"type": "min_words", "value": 2}))
        self.assertFalse(checks.run_check("one", {"type": "min_words", "value": 2}))

    def test_deterministic_score(self):
        specs = [{"type": "contains", "value": "yes"}, {"type": "contains", "value": "nope"}]
        self.assertEqual(checks.deterministic_score("yes indeed", specs), 5.0)
        self.assertIsNone(checks.deterministic_score("anything", []))
        # a malformed check counts as failed and never raises
        self.assertEqual(checks.deterministic_score("x", [{"type": "regex", "pattern": "("}]), 0.0)

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            checks.run_check("x", {"type": "nope"})


if __name__ == "__main__":
    unittest.main()
