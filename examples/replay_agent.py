#!/usr/bin/env python3
"""A stand-in agent for trying the harness without any model.

It ignores the prompt and replays a recorded answer for the task named in EVALS_TASK_ID,
so a run exercises the full path (suite, execution, scoring, history, dashboard) offline.
Point EVALS_AGENT_CMD at your own agent to grade it for real.

Optional: REPLAY_ANSWERS names another answers file (default: replay_answers.json beside this script).
"""
import json
import os
import sys
from pathlib import Path

path = Path(os.environ.get("REPLAY_ANSWERS") or Path(__file__).with_name("replay_answers.json"))
answers = json.loads(path.read_text())
sys.stdin.read()  # the prompt; unused
task = os.environ.get("EVALS_TASK_ID", "")
if task not in answers:
    sys.stderr.write("no recorded answer for task %r\n" % task)
    sys.exit(3)
sys.stdout.write(answers[task])
