import json
import stat
import sys
import unittest
from helpers import TempCase, ROOT  # noqa: F401
from arcturion_evals import judge


class TestParsing(unittest.TestCase):
    def test_score_is_clamped_to_range(self):
        self.assertEqual(judge.parse_score('thinking...\n{"score": 12, "rationale": "clamped"}\n'), (10.0, "clamped"))
        self.assertEqual(judge.parse_score('{"score": -3}')[0], 0.0)

    def test_unparseable(self):
        self.assertEqual(judge.parse_score("no json at all"), (None, ""))
        self.assertEqual(judge.parse_score('{"score": "high"}'), (None, ""))
        self.assertEqual(judge.parse_score('{"score": true}'), (None, ""))

    def test_complete_style_object(self):
        text = ('{"score": 8, "rationale": "solid", "style": {"brevity": 9, "plain_english": 7.5, '
                '"summary_format": 6, "decisiveness": 8}}')
        self.assertEqual(judge.parse_style(text),
                         {"brevity": 9.0, "plain_english": 7.5, "summary_format": 6.0, "decisiveness": 8.0})

    def test_style_values_are_clamped(self):
        text = '{"score": 5, "style": {"brevity": 99, "plain_english": -4, "summary_format": 5, "decisiveness": 5}}'
        style = judge.parse_style(text)
        self.assertEqual((style["brevity"], style["plain_english"]), (10.0, 0.0))

    def test_incomplete_or_missing_style_is_none_not_a_crash(self):
        self.assertIsNone(judge.parse_style('{"score": 8}'))
        self.assertIsNone(judge.parse_style('{"score": 8, "style": {"brevity": 9, "plain_english": 7}}'))
        self.assertIsNone(judge.parse_style("nothing useful"))

    def test_prompt_names_every_dimension_and_marks_output_untrusted(self):
        prompt = judge.build_prompt("task", "rubric", "output")
        for dim in judge.STYLE_DIMENSIONS:
            self.assertIn(dim, prompt)
        self.assertIn("untrusted", prompt)
        self.assertIn('"style"', prompt)

    def test_parse_reply_failure_carries_an_error(self):
        result = judge.parse_reply("sorry, no")
        self.assertIsNone(result["score"])
        self.assertIn("unparseable", result["error"])


class TestCommandJudge(TempCase):
    def script(self, body):
        path = self.root / "judge.py"
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return [sys.executable, str(path)]

    def test_prompt_goes_over_stdin_and_reply_is_parsed(self):
        argv = self.script(
            "import sys, json\n"
            "prompt = sys.stdin.read()\n"
            "ok = 'the answer' in prompt and 'the rubric' in prompt\n"
            "print(json.dumps({'score': 8 if ok else 1, 'rationale': 'stdin ok'}))\n")
        result = judge.CommandJudge(argv).judge("task", "the rubric", "the answer")
        self.assertEqual(result["score"], 8.0)
        self.assertIsNone(result["error"])

    def test_nonzero_exit_and_timeout_and_missing_binary_are_errors(self):
        self.assertIn("rc=3", judge.CommandJudge(self.script("import sys; sys.exit(3)")).judge("t", "r", "o")["error"])
        self.assertIn("timeout", judge.CommandJudge(self.script("import time; time.sleep(5)")).judge("t", "r", "o", timeout_s=1)["error"])
        self.assertIn("exec failed", judge.CommandJudge([str(self.root / "missing-binary")]).judge("t", "r", "o")["error"])

    def test_empty_command_is_rejected(self):
        with self.assertRaises(ValueError):
            judge.CommandJudge([])


class TestHTTPJudges(unittest.TestCase):
    def test_openai_shape_and_env_only_key(self):
        seen = {}

        def post(url, payload, headers, timeout):
            seen.update(url=url, payload=payload, headers=headers)
            return {"choices": [{"message": {"content": '{"score": 7, "rationale": "ok"}'}}]}
        j = judge.OpenAIJudge("judge-model", api_key_env="MY_KEY", base_url="http://127.0.0.1:9/v1/",
                              environ={"MY_KEY": "fixture-key-value"}, post=post)
        result = j.judge("task", "rubric", "output")
        self.assertEqual(result["score"], 7.0)
        self.assertEqual(seen["url"], "http://127.0.0.1:9/v1/chat/completions")
        self.assertEqual(seen["headers"], {"Authorization": "Bearer fixture-key-value"})
        self.assertNotIn("fixture-key-value", json.dumps(seen["payload"]))

    def test_anthropic_shape(self):
        seen = {}

        def post(url, payload, headers, timeout):
            seen.update(url=url, headers=headers)
            return {"content": [{"type": "text", "text": '{"score": 6}'}]}
        j = judge.AnthropicJudge("judge-model", environ={"ANTHROPIC_API_KEY": "fixture-key-value"}, post=post)
        self.assertEqual(j.judge("t", "r", "o")["score"], 6.0)
        self.assertEqual(seen["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(seen["headers"]["x-api-key"], "fixture-key-value")

    def test_missing_key_never_calls_out(self):
        def post(*a):
            raise AssertionError("must not call the network without a key")
        result = judge.OpenAIJudge("m", environ={}, post=post).judge("t", "r", "o")
        self.assertIsNone(result["score"])
        self.assertIn("OPENAI_API_KEY", result["error"])
        self.assertNotIn("fixture", result["error"])

    def test_transport_errors_become_judge_errors(self):
        def post(*a):
            raise OSError("connection refused")
        result = judge.OpenAIJudge("m", environ={"OPENAI_API_KEY": "fixture-key-value"}, post=post).judge("t", "r", "o")
        self.assertIsNone(result["score"])
        self.assertIn("OSError", result["error"])

    def test_base_url_must_be_http(self):
        with self.assertRaises(ValueError):
            judge.OpenAIJudge("m", base_url="file:///etc", environ={})

    def test_model_is_required(self):
        with self.assertRaises(ValueError):
            judge.OpenAIJudge("", environ={})


class TestSelection(unittest.TestCase):
    def test_default_is_no_judge(self):
        self.assertIsNone(judge.make_judge({}))
        self.assertIsNone(judge.make_judge({"EVALS_JUDGE": "none"}))

    def test_selection_by_environment(self):
        env = {"EVALS_JUDGE": "openai", "EVALS_JUDGE_MODEL": "m", "EVALS_JUDGE_API_KEY_ENV": "OTHER_KEY",
               "EVALS_JUDGE_BASE_URL": "http://127.0.0.1:1/v1"}
        j = judge.make_judge(env)
        self.assertIsInstance(j, judge.OpenAIJudge)
        self.assertEqual(j.api_key_env, "OTHER_KEY")
        self.assertIsInstance(judge.make_judge({"EVALS_JUDGE": "anthropic", "EVALS_JUDGE_MODEL": "m"}), judge.AnthropicJudge)
        self.assertIsInstance(judge.make_judge({"EVALS_JUDGE": "command", "EVALS_JUDGE_CMD": "cat"}), judge.CommandJudge)

    def test_unknown_judge_is_an_error(self):
        with self.assertRaises(ValueError):
            judge.make_judge({"EVALS_JUDGE": "mystery"})

    def test_judge_output_never_raises(self):
        class Boom:
            def judge(self, *a):
                raise RuntimeError("private detail")
        out = judge.judge_output(Boom(), "t", "r", "o")
        self.assertIsNone(out["score"])
        self.assertNotIn("private detail", out["error"])
        self.assertEqual(judge.judge_output(None, "t", "r", "o")["error"], None)

        class Odd:
            def judge(self, *a):
                return "not a dict"
        self.assertIn("non-dict", judge.judge_output(Odd(), "t", "r", "o")["error"])


if __name__ == "__main__":
    unittest.main()
