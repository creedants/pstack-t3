if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import os

from .support import (
    setUpModule,
    BrigadeTest,
    Path,
    SCRIPT,
    call,
    call_in_process,
    run_globals,
)
from .seat_launch.fixtures import CATALOG, PARENT, XHIGH_SEAT
from .seat_launch.test_roles_py import RolesPyMixin
from .seat_launch.test_seat_choice import SeatChoiceMixin


class LaunchTest(BrigadeTest, SeatChoiceMixin, RolesPyMixin):
    def fire_station(self, station):
        ticket = self.brigade("ticket", "add", "--kind", "fix", "--summary", station)
        return self.brigade("fire", "--tickets", ticket, "--station", station, "--summary", station)

    def open_wide(self, **extra):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "8", *extra)

    def launch_call(self, dish="D1", catalog=None, parent=PARENT, in_process=False):
        catalog = CATALOG if catalog is None else catalog
        runner = call_in_process if in_process else call
        return runner(self, SCRIPT, [
            "--store", str(self.store), "--at", str(self.at),
            "launch", dish, "--parent", parent, "--catalog", str(catalog),
        ])

    def assert_launch(self, expected, dish="D1", catalog=None, in_process=False):
        outcome = self.launch_call(dish, catalog, in_process=in_process)
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text(), expected + "\n")
        self.assertEqual(outcome.stderr_text(), "")
        return outcome

    def assert_refused(self, expected, dish="D1", catalog=None, in_process=False):
        outcome = self.launch_call(dish, catalog, in_process=in_process)
        self.assertEqual(outcome.code, 1, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text(), "")
        self.assertEqual(outcome.stderr_text().strip(), expected)
        return outcome

    def write_brief(self, text, dish="D1"):
        folder = self.at / "briefs"
        folder.mkdir(exist_ok=True)
        path = folder / f"{dish}.md"
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text)
        return path

    def write_user_roles(self, roles, budget="default", mode=None):
        directory = Path(os.environ["XDG_CONFIG_HOME"]) / "pstack-t3"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "roles.json"
        document = {"version": 1, "budget": budget, "roles": roles}
        if mode is not None:
            document["mode"] = mode
        path.write_text(json.dumps(document))
        return path

    def write_launch_seats(self, *roles, mode=None):
        """Name the Grok xhigh seat for each role, so a full seat prints xhigh and light caps it to medium. Mode, when given, is written to the user roles file; omitted, the file has no mode."""
        self.write_user_roles({role: [XHIGH_SEAT] for role in roles}, mode=mode)

    def write_project_roles(self, roles, budget="default"):
        directory = self.project / ".pstack"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "t3-roles.json"
        path.write_text(json.dumps({"version": 1, "budget": budget, "roles": roles}))
        return path

    def entry_globals(self):
        return run_globals(self, SCRIPT)

    def install_show(self, text="", code=0, stderr=""):
        script = Path(self.temporary.name) / "show-roles.py"
        script.write_text(
            "import sys\n"
            f"sys.stderr.write({stderr!r})\n"
            f"code = {code}\n"
            "if code:\n"
            "    raise SystemExit(code)\n"
            f"sys.stdout.write({text!r})\n"
        )
        glob = self.entry_globals()
        glob["BUILT_ROLES"] = script
        glob["SOURCE_ROLES"] = script
        return script

    def show_payload(self, payload):
        self.install_show(text=json.dumps(payload))
