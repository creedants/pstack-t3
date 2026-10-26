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

MUSE = "opencode-go/muse-spark-1.3-contributor"

DEEPSEEK = "opencode-go/deepseek-v4.1-flash"

STEP = "opencode-go/step-5-preview-free"

BEAM = "opencode-go/beam-free"

BUNNY = "opencode-go/bunny-free"


def opencode_model(model_id, **fields):
    return {"id": model_id, "options": [], **fields}


def opencode_catalog(*models):
    return {"providers": [
        {
            "providerInstanceId": GO,
            "driverKind": "opencode",
            "canRunChildTask": True,
            "constraints": [],
            "models": list(models),
        },
    ]}


def go_seat(model):
    return {"providerInstanceId": GO, "model": model}


class MissingModelFallbackCase(unittest.TestCase):
    """The substitute for a configured seat's missing model honors the allow list and the exclude list."""

    def show(self, catalog, roles_file, parent):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, catalog)
            repo.put(repo.user, roles_file)
            completed = repo.run(
                "show", "--catalog", str(path), "--parent", parent, "--role", "bug-fix", "--brief-mode", "light",
            )
        return completed

    def user_document(self, roles, allow, exclude=None):
        document = {"version": 1, "budget": "default", "mode": "full", "roles": roles, "opencodeAllow": allow}
        if exclude is not None:
            document["exclude"] = exclude
        return document


class MissingModelSubstituteTest(MissingModelFallbackCase):
    def test_show_never_substitutes_a_model_the_allow_list_refuses(self):
        catalog = opencode_catalog(opencode_model(BEAM), opencode_model(DEEPSEEK))
        roles_file = self.user_document({"bug-fix": [go_seat(MUSE)]}, [MUSE, DEEPSEEK])
        completed = self.show(catalog, roles_file, f"{GO}/{DEEPSEEK}")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [go_seat(DEEPSEEK)])
        self.assertEqual(
            entry["notes"],
            [f"{GO}/{MUSE} is not in the catalog; using {DEEPSEEK}"],
        )

    def test_show_prefers_the_parents_allowed_model_over_the_first_allowed_one(self):
        catalog = opencode_catalog(
            opencode_model(BEAM), opencode_model(STEP), opencode_model(DEEPSEEK),
        )
        roles_file = self.user_document({"bug-fix": [go_seat(MUSE)]}, [MUSE, STEP, DEEPSEEK])
        completed = self.show(catalog, roles_file, f"{GO}/{DEEPSEEK}")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [go_seat(DEEPSEEK)])
        self.assertEqual(
            entry["notes"],
            [f"{GO}/{MUSE} is not in the catalog; using {DEEPSEEK}"],
        )

    def test_show_refuses_a_role_when_every_substitute_is_outside_the_allow_list(self):
        catalog = opencode_catalog(opencode_model(BEAM))
        roles_file = self.user_document({"bug-fix": [go_seat(MUSE)]}, [MUSE])
        completed = self.show(catalog, roles_file, f"{GO}/{DEEPSEEK}")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertTrue(completed.stderr.startswith("error: "), completed.stderr)
        self.assertIn("allow list", completed.stderr)
        self.assertIn(BEAM, completed.stderr)

    def test_a_free_model_is_a_substitute_when_the_allow_list_does_not_name_it(self):
        catalog = opencode_catalog(
            opencode_model(BEAM), opencode_model(BUNNY, cost=0), opencode_model(DEEPSEEK),
        )
        roles_file = self.user_document({"bug-fix": [go_seat(MUSE)]}, [MUSE, DEEPSEEK])
        completed = self.show(catalog, roles_file, "claudeAgent/claude-opus-5-5")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [go_seat(BUNNY)])
        self.assertEqual(
            entry["notes"],
            [f"{GO}/{MUSE} is not in the catalog; using {BUNNY}"],
        )


class InheritReplacementAllowTest(MissingModelFallbackCase):
    MUSE_EXCLUDED = [{"model": "muse-spark-1.3-contributor"}]

    def test_inherit_replacement_never_picks_a_model_the_allow_list_refuses(self):
        catalog = opencode_catalog(opencode_model(BEAM), opencode_model(DEEPSEEK))
        roles_file = self.user_document({}, [MUSE, DEEPSEEK], self.MUSE_EXCLUDED)
        completed = self.show(catalog, roles_file, f"{GO}/{MUSE}")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], go_seat(DEEPSEEK))
        self.assertTrue(entry["notes"][0].startswith(f"inherit replaced by {GO}/{DEEPSEEK}: "), entry)

    def test_inherit_refuses_when_every_replacement_is_outside_the_allow_list(self):
        catalog = opencode_catalog(opencode_model(BEAM))
        roles_file = self.user_document({}, [MUSE], self.MUSE_EXCLUDED)
        completed = self.show(catalog, roles_file, f"{GO}/{MUSE}")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertTrue(completed.stderr.startswith("error: "), completed.stderr)
        self.assertIn("allow list", completed.stderr)
        self.assertIn(BEAM, completed.stderr)
