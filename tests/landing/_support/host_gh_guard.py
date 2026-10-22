import json
import os
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[2]

SHIM = """#!{python}
import json, os, sys
from pathlib import Path

shim = Path(__file__).resolve()
directory = shim.parent
stubs = (Path({temporary!r}).resolve(), Path({tests!r}).resolve())

for entry in os.environ.get("PATH", "").split(os.pathsep):
    if not entry:
        continue
    candidate = Path(entry) / "gh"
    if not (candidate.is_file() and os.access(candidate, os.X_OK)):
        continue
    resolved = candidate.resolve()
    if resolved == shim or resolved.parent == directory:
        continue
    if any(root == resolved or root in resolved.parents for root in stubs):
        os.execv(str(resolved), [str(resolved), *sys.argv[1:]])
    break
with open(Path({log!r}), "a") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(127)
"""


class HostGhGuard:
    """Fail a test that reaches the gh shim with no stub of its own, and one whose stub wrote a call to the unanswered log.

    A test that rewrites PATH to drop the shim, or runs an absolute host path, is outside the guard.
    """

    def __init__(self, base, label="landing"):
        self.label = label
        self.directory = Path(base) / "host-gh-guard"
        self.script = self.directory / "bin" / "gh"
        self.log = self.directory / "calls"
        self.unanswered = self.directory / "unanswered"

    def install(self):
        self.script.parent.mkdir(parents=True)
        self.script.write_text(SHIM.format(
            python=sys.executable,
            temporary=str(self.directory.parent.resolve()),
            tests=str(TESTS),
            log=str(self.log)))
        self.script.chmod(0o755)
        self.log.touch()
        self.unanswered.touch()
        os.environ["PATH"] = str(self.script.parent) + os.pathsep + os.environ.get("PATH", "")

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def assert_unused(self):
        calls = self.calls()
        if calls:
            raise AssertionError(
                f"this {self.label} test called gh with no stub of its own; recorded calls: {calls}")

    def assert_answered(self):
        calls = [json.loads(line) for line in self.unanswered.read_text().splitlines() if line]
        if calls:
            raise AssertionError(
                f"this {self.label} gh stub had no answer for {calls[0]}; "
                f"unanswered calls: {calls}")
