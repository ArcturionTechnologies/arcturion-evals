"""Shared test helpers: temp-dir configs and a tiny stand-in agent. No network, no real state."""
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arcturion_evals.config import Config  # noqa: E402


class TempCase(unittest.TestCase):
    """Gives each test its own temp root and a Config that points only inside it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Resolve once: on macOS the temp dir is /var/... which is a symlink to /private/var/...
        self.root = Path(self.tmp.name).resolve()
        self.suites = self.root / "suites"
        self.state = self.root / "state"
        self.suites.mkdir()

    def config(self, **kw):
        values = dict(suites_dir=self.suites, state_dir=self.state, environ={})
        values.update(kw)
        return Config(**values)

    def write_suite(self, name="demo", tasks=None, key="suite", filename=None):
        tasks = tasks if tasks is not None else [
            {"id": "t1", "prompt": "say x", "checks": [{"type": "contains", "value": "x"}]},
            {"id": "t2", "prompt": "say x", "checks": [{"type": "contains", "value": "x"}]}]
        import json
        (self.suites / (filename or name + ".json")).write_text(json.dumps({key: name, "tasks": tasks}))

    def agent_script(self, body):
        """Write a tiny Python agent; returns the argv that runs it."""
        path = self.root / "agent.py"
        path.write_text(textwrap.dedent(body))
        return [sys.executable, str(path)]


def under(path, root):
    """True if `path` is inside `root` after resolving symlinks (/var vs /private/var safe)."""
    return os.path.realpath(path).startswith(os.path.realpath(root) + os.sep)
