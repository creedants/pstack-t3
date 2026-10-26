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
import tempfile
import unittest
from .support import Repo, setUpModule


PROVIDER = "opencode-go"
MODEL = "deepseek-v4.1-flash"
PARENT = f"{PROVIDER}/{MODEL}"


def _select(option_id, values):
    return {"id": option_id, "type": "select", "options": [{"id": value} for value in values]}


def _catalog(values):
    return {"providers": [{
        "providerInstanceId": PROVIDER,
        "driverKind": "opencode",
        "canRunChildTask": True,
        "constraints": [],
        "models": [{
            "id": MODEL,
            "options": [_select("variant", values)],
            "cost": 0,
        }],
    }]}


def _seat(variant=None):
    seat = {"providerInstanceId": PROVIDER, "model": MODEL}
    if variant is not None:
        seat["options"] = {"variant": variant}
    return seat


def _show(catalog, roles_file, parent=PARENT, brief_mode="light", role="bug-fix"):
    with tempfile.TemporaryDirectory() as directory:
        repo = Repo(directory)
        path = repo.directory / "catalog.json"
        repo.put(path, catalog)
        repo.put(repo.user, roles_file)
        completed = repo.run(
            "show", "--catalog", str(path), "--parent", parent,
            "--role", role, "--brief-mode", brief_mode,
        )
    return completed


class VariantCapTest(unittest.TestCase):
    def test_light_caps_variant_max_to_medium(self):
        catalog = _catalog(["low", "medium", "high", "max"])
        roles_file = {"roles": {"bug-fix": [_seat("max")]}}
        completed = _show(catalog, roles_file)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        seats = json.loads(completed.stdout)["roles"]["bug-fix"]["seats"]
        self.assertEqual(seats, [_seat("medium")])

    def test_light_caps_variant_max_to_low_when_medium_missing(self):
        catalog = _catalog(["low", "high", "max"])
        roles_file = {"roles": {"bug-fix": [_seat("max")]}}
        completed = _show(catalog, roles_file)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        seats = json.loads(completed.stdout)["roles"]["bug-fix"]["seats"]
        self.assertEqual(seats, [_seat("low")])

    def test_light_gives_unnamed_variant_seat_the_cap(self):
        catalog = _catalog(["low", "medium", "high", "max"])
        roles_file = {"roles": {"bug-fix": [_seat()]}}
        completed = _show(catalog, roles_file)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        seats = json.loads(completed.stdout)["roles"]["bug-fix"]["seats"]
        self.assertEqual(seats, [_seat("medium")])

    def test_large_budget_caps_variant_max_to_xhigh(self):
        catalog = _catalog(["low", "medium", "high", "xhigh", "max"])
        roles_file = {"budget": "large", "roles": {"bug-fix": [_seat("max")]}}
        completed = _show(catalog, roles_file, brief_mode="full")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        seats = json.loads(completed.stdout)["roles"]["bug-fix"]["seats"]
        self.assertEqual(seats, [_seat("xhigh")])

    def test_light_leaves_non_ladder_variant_alone(self):
        catalog = _catalog(["none", "thinking"])
        roles_file = {"roles": {"bug-fix": [_seat("thinking")]}}
        completed = _show(catalog, roles_file)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        seats = json.loads(completed.stdout)["roles"]["bug-fix"]["seats"]
        self.assertEqual(seats, [_seat("thinking")])

    def test_light_keeps_verifiers_variant_at_max(self):
        catalog = _catalog(["low", "medium", "high", "max"])
        roles_file = {"roles": {"verifiers": [_seat("max")]}}
        completed = _show(catalog, roles_file, role="verifiers")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        seats = json.loads(completed.stdout)["roles"]["verifiers"]["seats"]
        self.assertEqual(seats, [_seat("max")])

    def test_light_inherit_on_variant_parent_becomes_explicit_medium(self):
        catalog = _catalog(["low", "medium", "high", "max"])
        roles_file = {"roles": {"bug-fix": ["inherit"]}}
        completed = _show(catalog, roles_file)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [_seat("medium")])
        self.assertEqual(
            entry["info"],
            [f"inherit made explicit as {PARENT} so the small budget applies"],
        )


if __name__ == "__main__":
    unittest.main()
