# Design notes

**What it is.** A harness that runs fixed tasks against an agent and scores the answers. It
measures answer *quality*. It is separate from anything that measures whether an agent's
workspace is healthy.

## Layout

```
arcturion_evals/checks.py        deterministic checks
arcturion_evals/judge.py         optional judge: command, OpenAI-compatible, or Anthropic
arcturion_evals/config.py        every path and command is a setting
arcturion_evals/runner.py        suite loading, rotation, execution, scoring, history
arcturion_evals/dashboard.py     HTML trend page and weekly line
arcturion_evals/style_report.py  response-style report
arcturion_evals/intake.py        optional extra cases from a JSONL file
suites/<name>.json               the tasks
```

## Task schema

```json
{ "suite": "arithmetic", "version": 1, "description": "...",
  "tasks": [ {
    "id": "arith-001", "title": "Invoice total with tax",
    "prompt": "what the agent is asked, with all data inline",
    "model": "optional label recorded with the result",
    "timeout_s": 120,
    "checks": [ {"type": "number_close", "expect": 186.84, "tol": 0.01} ],
    "rubric": "optional criteria for the judge",
    "weight": 1.0 } ] }
```

Check types: `regex`, `not_regex`, `contains`, `not_contains` (optional `"flags": "i"`),
`number_close` (`expect`, `tol`), `json_field` (`path`, `equals`; reads the last JSON object in the
output), `max_words`, `min_words`. A malformed check counts as failed and never crashes a run.

## Scoring

- Deterministic checks: pass fraction x 10. Free and exact.
- A task with a `rubric` and a configured judge also gets a judge score from 0 to 10.
- Final score: the deterministic score, the judge score, or their mean when both exist.
- A crash, timeout or non-zero exit scores 0 with the error recorded. A crashed agent is a
  regression signal, so it is not skipped.
- A judge failure falls back to the deterministic score and records `judge_error`.
- If **every** task in a run errors, the run is quarantined (`*.failed`) so it cannot poison the
  trend history, and the receipt says `failed`.

## State (all under `EVALS_STATE_DIR`)

```
history/<date>_<run>.jsonl    append-only, one row per task; written as each task finishes
rotation.json                 pointer over the flattened task list
latest_run.json               receipt of the last run
dashboard.html                regenerated after each real run
```

Nothing is written anywhere else. A dry run writes nothing at all to this folder.

## Rotation

`run --slice N` takes the next N tasks from a stable, sorted list and advances a wrapping
pointer, so a nightly slice covers the whole set over time. `--suite` and `--task` select
directly and do not move the pointer.

## Style dimensions

When a judge is configured it also scores four response-style dimensions from 0 to 10:
brevity, plain English, summary format and decisiveness. `report` aggregates them per suite and per
model label and lists review candidates. It never changes anything and always exits 0.
