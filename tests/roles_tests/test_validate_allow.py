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


GO = "opencode"
METERED = "opencode/metered-model"
ALLOWED = "opencode/allowed-model"
MISSING = "opencode/missing-model"
EXCLUDED = "opencode/excluded-model"


def opencode_model(model_id):
    return {"id": model_id, "options": []}


def opencode_catalog(*models):
    return {"providers": [{
        "providerInstanceId": GO,
        "driverKind": "opencode",
        "canRunChildTask": True,
        "constraints": [],
        "models": list(models),
    }]}


def go_seat(model):
    return {"providerInstanceId": GO, "model": model}


def user_document(roles, allow, exclude=None):
    document = {"version": 1, "budget": "default", "mode": "full", "roles": roles, "opencodeAllow": allow}
    if exclude is not None:
        document["exclude"] = exclude
    return document


def run_show(catalog, roles_file, parent):
    with tempfile.TemporaryDirectory() as directory:
        repo = Repo(directory)
        path = repo.directory / "catalog.json"
        repo.put(path, catalog)
        repo.put(repo.user, roles_file)
        completed = repo.run(
            "show", "--catalog", str(path), "--parent", parent,
            "--role", "bug-fix", "--brief-mode", "light",
        )
    return completed


def run_validate(catalog, roles_file, parent):
    with tempfile.TemporaryDirectory() as directory:
        repo = Repo(directory)
        path = repo.directory / "catalog.json"
        repo.put(path, catalog)
        repo.put(repo.user, roles_file)
        completed = repo.run("validate", "--catalog", str(path), "--parent", parent)
    return completed


class ValidateAllowTest(unittest.TestCase):
    def test_validate_names_same_substitute_show_picks(self):
        catalog = opencode_catalog(opencode_model(METERED), opencode_model(ALLOWED))
        roles_file = user_document({"bug-fix": [go_seat(MISSING)]}, [ALLOWED])
        parent = "claudeAgent/claude-opus-5-5"
        shown = run_show(catalog, roles_file, parent)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        entry = json.loads(shown.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [go_seat(ALLOWED)])
        self.assertIn(f"using {ALLOWED}", entry["notes"][0])
        validated = run_validate(catalog, roles_file, parent)
        self.assertEqual(validated.returncode, 1, validated.stdout)
        self.assertIn(f"using {ALLOWED}", validated.stdout)
        self.assertNotIn(METERED, validated.stdout)

    def test_validate_refuses_inherit_when_replacements_outside_allow(self):
        catalog = opencode_catalog(opencode_model(METERED))
        roles_file = user_document({"bug-fix": ["inherit"]}, [ALLOWED], [{"model": "excluded-model"}])
        parent = f"{GO}/{EXCLUDED}"
        shown = run_show(catalog, roles_file, parent)
        self.assertEqual(shown.returncode, 2)
        self.assertEqual(shown.stdout, "")
        self.assertIn("allow list", shown.stderr)
        validated = run_validate(catalog, roles_file, parent)
        self.assertEqual(validated.returncode, 1, validated.stdout)
        self.assertIn("has no model the exclude list and the allow list allow", validated.stdout)

    def test_validate_ok_when_substitute_and_replacement_allowed(self):
        catalog = opencode_catalog(opencode_model(ALLOWED))
        roles_file = user_document({"bug-fix": [go_seat(ALLOWED)]}, [ALLOWED])
        parent = "claudeAgent/claude-opus-5-5"
        shown = run_show(catalog, roles_file, parent)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["roles"]["bug-fix"]["seats"], [go_seat(ALLOWED)])
        validated = run_validate(catalog, roles_file, parent)
        self.assertEqual(validated.returncode, 0, validated.stdout)
        self.assertEqual(validated.stdout, "ok\n")
        inherit_roles = user_document({"bug-fix": ["inherit"]}, [ALLOWED], [{"model": "excluded-model"}])
        inherit_parent = f"{GO}/{EXCLUDED}"
        inherit_catalog = opencode_catalog(opencode_model(ALLOWED))
        inherit_shown = run_show(inherit_catalog, inherit_roles, inherit_parent)
        self.assertEqual(inherit_shown.returncode, 0, inherit_shown.stderr)
        inherit_validated = run_validate(inherit_catalog, inherit_roles, inherit_parent)
        self.assertEqual(inherit_validated.returncode, 0, inherit_validated.stdout)
        self.assertEqual(inherit_validated.stdout, "ok\n")


if __name__ == "__main__":
    unittest.main()
