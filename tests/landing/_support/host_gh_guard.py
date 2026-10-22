import os
import sys
from pathlib import Path

SHIM = """#!{python}
import os, sys
from pathlib import Path
here = Path(__file__).resolve().parent
with open(here.parent / "calls", "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
for entry in os.environ.get("PATH", "").split(os.pathsep):
    if not entry or Path(entry).resolve() == here:
        continue
    candidate = Path(entry) / "gh"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        os.execv(str(candidate), [str(candidate), *sys.argv[1:]])
sys.exit(127)
"""


class HostGhGuard:
    """Fail a landing test that runs the host gh.

    install() puts a gh shim first on PATH. The shim records the call, then runs the next gh
    on PATH, so a test that names its own gh or sets LAND_GH keeps the behavior it had.
    """

    def __init__(self, base):
        self.directory = Path(base) / "host-gh-guard"
        self.script = self.directory / "bin" / "gh"
        self.log = self.directory / "calls"

    def install(self):
        self.script.parent.mkdir(parents=True)
        self.script.write_text(SHIM.format(python=sys.executable))
        self.script.chmod(0o755)
        self.log.touch()
        os.environ["PATH"] = str(self.script.parent) + os.pathsep + os.environ.get("PATH", "")

    def calls(self):
        return [line for line in self.log.read_text().splitlines() if line.strip()]

    def assert_unused(self):
        calls = self.calls()
        if calls:
            raise AssertionError(
                f"this landing test ran the host gh {len(calls)} time(s); "
                f"stub it with self.fake_gh() or a gh of the test's own: {calls}")
