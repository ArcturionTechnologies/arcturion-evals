# ArcturionEvals

A small eval harness for AI agents. It runs fixed tasks against any agent, scores the answers, and
keeps a history so you can see whether quality is going up or down over time.

Agents change quietly: a new prompt, a different model, a tool update. Without a fixed set of
questions with known right answers, nobody notices a regression until a person trips over it.
ArcturionEvals gives you that set, runs a rotating slice of it on a schedule, and draws the trend.

Python 3.11+, standard library only. **No model, network or API key is needed to run it.**

> **Portfolio project.** This is an open-source sample of the tooling behind Arcturion's
> multi-agent setup. It is not a commercial product and makes no claims about revenue or
> customers. All bundled tasks are synthetic and generic.

## Quickstart

```bash
git clone https://github.com/ArcturionTechnologies/arcturion-evals.git
cd arcturion-evals

python3 -m arcturion_evals list | head -3
# arithmetic/arith-001   Invoice total with tax
# ...

# Try the full path offline with a stand-in agent that replays recorded answers.
python3 -m arcturion_evals --agent-cmd "python3 examples/replay_agent.py" run --slice 48
# run d2f2556b done: mean 10.00 over 48 task(s) -> evals-state/history/<date>_<run>.jsonl

python3 -m arcturion_evals dashboard    # evals-state/dashboard.html
python3 -m arcturion_evals digest       # one plain-English line
```

To grade a real agent, give it as a command. The task prompt arrives on **stdin** and the answer is
read from **stdout**, so any script, CLI or model wrapper works:

```bash
export EVALS_AGENT_CMD="/path/to/your-agent-wrapper"
python3 -m arcturion_evals run --slice 15
```

Or install it as a command: `pip install .`, then run `arcturion-evals ...`.

## What is in the box

48 synthetic tasks in six suites. Every prompt carries its own data, nothing touches files or tools,
and every task has at least one exact, automatic check.

| Suite | Tests | Example |
| --- | --- | --- |
| `arithmetic` | exact math and units | invoice total with tax, stacked discounts |
| `extraction` | pulling facts from short text | count error lines, convert config to JSON |
| `instructions` | obeying format and length rules | exactly three bullets, JSON only, 20-word limit |
| `reasoning` | logic, sequences, reading code | ordering puzzle, spot the failing line |
| `summarization` | faithful summaries and rewrites | no invented figures, flag conflicting sources |
| `boundaries` | safe, honest behavior | confirm before deleting, ignore text injected into data, admit missing data |

Add your own by dropping a JSON file in `suites/` (schema in [docs/DESIGN.md](docs/DESIGN.md)).
Keep answers stable over time: frozen facts and exact math, never live state.

## How scoring works

- **Deterministic checks** (regex, contains, close number, JSON field, word limits) are free and exact.
  Score is the fraction passed, times 10.
- **Optional judge.** Tasks with a `rubric` can also be scored 0 to 10 by an LLM judge. The final
  score is the mean of the two. With no judge, the deterministic score stands alone.
- **Failures count.** A crash, timeout or non-zero exit scores 0 and is recorded. If *every* task in
  a run fails, the run is set aside so it cannot pollute the trend line.

## Choosing a judge (all optional)

Set `EVALS_JUDGE` to one of these. Settings are environment variables; keys stay in your environment.

| `EVALS_JUDGE` | What it does | Other settings |
| --- | --- | --- |
| `none` (default) | deterministic checks only | |
| `command` | sends the judging prompt to your command on stdin, reads the reply on stdout | `EVALS_JUDGE_CMD` |
| `openai` | OpenAI-compatible Chat Completions, including local servers | `EVALS_JUDGE_MODEL`, `EVALS_JUDGE_API_KEY_ENV` (name of the variable holding the key), `EVALS_JUDGE_BASE_URL` |
| `anthropic` | Messages API | same three settings |

Use a judge from a different model family than the agent you are grading; a model tends to grade its
own family generously. A judge that fails never stops a run: the task falls back to its
deterministic score and the error is recorded.

With a judge, each task is also scored on four response-style dimensions (brevity, plain English,
summary format, decisiveness). `python3 -m arcturion_evals report` aggregates them and lists models
that look weak, for a person to review. It changes nothing.

## Configuration

Flags win over environment variables, which win over defaults.

| Flag | Environment | Default |
| --- | --- | --- |
| `--suites-dir` | `EVALS_SUITES_DIR` | `./suites` |
| `--state-dir` | `EVALS_STATE_DIR` | `./evals-state` |
| `--agent-cmd` | `EVALS_AGENT_CMD` | none (required to run for real) |
| `--workdir` | `EVALS_WORKDIR` | current directory |
| `--extra-cases` | `EVALS_EXTRA_CASES` | none |
| `--notify-cmd` | `EVALS_NOTIFY_CMD` | none |

The agent command also receives `EVALS_TASK_ID`, `EVALS_SUITE` and `EVALS_MODEL` in its environment.
Everything the harness writes goes under the state folder, and nothing else:

```
<state>/history/<date>_<run>.jsonl   one row per task, appended as each finishes
<state>/rotation.json                where the next slice starts
<state>/latest_run.json              receipt of the last run
<state>/dashboard.html               trend page
```

`run --dry-run` executes nothing and writes nothing. `run --suite NAME` or `--task ID` selects directly.

## Notifications and scheduling

`--notify-cmd` is any command that reads one line on stdin: a chat webhook script, `mail`, a log
appender. It receives the weekly digest on Sundays and a one-line notice when a run fails.
`examples/launchd/com.example.arcturion-evals.plist` is a macOS template for a nightly slice.

## Extra cases from ArcturionFlywheel

`--extra-cases` accepts the `eval_cases` file written by
[arcturion-flywheel](https://github.com/ArcturionTechnologies/arcturion-flywheel), or any JSONL in the
same shape. They are judge-scored, never copied into `suites/`, and thin stubs are skipped with a log line.

## Project layout

```
arcturion_evals/    checks, judge, config, runner, dashboard, style report, intake, cli
suites/             the 48 synthetic tasks
examples/           replay agent and its recorded answers, launchd template
docs/DESIGN.md      schema, scoring and state details
tests/              89 tests, stdlib unittest
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Everything runs in temporary folders with local stand-in agents: no network, no credentials. One test
runs a full real run with `HOME` pointed at an empty folder and asserts nothing was written outside
the configured state folder.

## License

MIT. See [LICENSE](LICENSE).

Implementation is AI-assisted; architecture, requirements, and testing directed by Robert Lingoes.
