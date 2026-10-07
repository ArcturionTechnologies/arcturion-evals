"""Settings for one eval run. Everything that used to be a fixed path or vendor is a setting here.

Resolution order for each setting: explicit argument, then environment variable, then default.

    EVALS_SUITES_DIR   folder of suite JSON files            default ./suites
    EVALS_STATE_DIR    history, rotation pointer, receipts   default ./evals-state
    EVALS_WORKDIR      working directory for the agent       default: the current directory
    EVALS_AGENT_CMD    command that runs the agent under test (prompt arrives on stdin)
    EVALS_EXTRA_CASES  optional JSONL of extra cases (see intake.py)
    EVALS_NOTIFY_CMD   optional command that receives the weekly digest line on stdin
    EVALS_JUDGE        none (default) | command | openai | anthropic   (see judge.py)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import os
import shlex

DEFAULT_SLICE = 15
DEFAULT_TIMEOUT_S = 300


def _split(value):
    if not value:
        return None
    parts = shlex.split(value)
    return parts or None


@dataclass
class Config:
    suites_dir: Path = Path("suites")
    state_dir: Path = Path("evals-state")
    workdir: Path | None = None
    agent_cmd: list | None = None
    extra_cases: Path | None = None
    notify_cmd: list | None = None
    judge: object = None  # a judge object (see judge.py) or None for deterministic-only scoring
    environ: dict = field(default_factory=lambda: dict(os.environ), repr=False)

    @property
    def history_dir(self):
        return Path(self.state_dir) / "history"

    @property
    def rotation_file(self):
        return Path(self.state_dir) / "rotation.json"

    @property
    def receipt_file(self):
        return Path(self.state_dir) / "latest_run.json"

    @property
    def dashboard_path(self):
        return Path(self.state_dir) / "dashboard.html"

    @classmethod
    def from_env(cls, environ=None, **overrides):
        from .judge import make_judge
        env = dict(os.environ if environ is None else environ)

        def pick(name, env_name, default=None, cast=lambda v: v):
            if overrides.get(name) is not None:
                return cast(overrides[name])
            if env.get(env_name):
                return cast(env[env_name])
            return default

        agent_cmd = overrides.get("agent_cmd")
        if isinstance(agent_cmd, str):
            agent_cmd = _split(agent_cmd)
        notify_cmd = overrides.get("notify_cmd")
        if isinstance(notify_cmd, str):
            notify_cmd = _split(notify_cmd)
        return cls(
            suites_dir=pick("suites_dir", "EVALS_SUITES_DIR", Path("suites"), Path),
            state_dir=pick("state_dir", "EVALS_STATE_DIR", Path("evals-state"), Path),
            workdir=pick("workdir", "EVALS_WORKDIR", None, Path),
            agent_cmd=agent_cmd or _split(env.get("EVALS_AGENT_CMD")),
            extra_cases=pick("extra_cases", "EVALS_EXTRA_CASES", None, Path),
            notify_cmd=notify_cmd or _split(env.get("EVALS_NOTIFY_CMD")),
            judge=overrides["judge"] if overrides.get("judge") is not None else make_judge(env),
            environ=env,
        )
