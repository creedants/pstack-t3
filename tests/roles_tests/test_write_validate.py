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


def _variant_catalog():
    return {"providers": [{
        "providerInstanceId": PROVIDER,
        "driverKind": "opencode",
        "canRunChildTask": True,
        "constraints": [],
        "models": [{
            "id": MODEL,
            "options": [_select("variant", ["none", "high", "max"])],
            "cost": 0,
        }],
    }]}


def _seat(variant):
    return {"providerInstanceId": PROVIDER, "model": MODEL, "options": {"variant": variant}}


def _roles_file(mode, seats, budget="default"):
    document = {
        "roles": {"bug-fix": seats},
        "exclude": [{"options": {"variant": "none"}}],
    }
    if mode is not None:
        document["mode"] = mode
    if budget is not None:
        document["budget"] = budget
    return document


def _put_catalog(repo):
    path = repo.directory / "catalog.json"
    repo.put(path, _variant_catalog())
    return path


def _write(repo, catalog_path, *args):
    return repo.run("write", "--catalog", str(catalog_path), "--parent", PARENT, *args)


def _validate(repo, catalog_path):
    return repo.run("validate", "--catalog", str(catalog_path), "--parent", PARENT)


class WriteValidateTest(unittest.TestCase):
    def test_user_write_mode_merge_matches_validate(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog_path = _put_catalog(repo)
            repo.put(repo.user, _roles_file("light", [_seat("max")]))
            repo.put(repo.project, {"mode": "full", "roles": {}})
            good = _write(repo, catalog_path, "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max")
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertEqual(_validate(repo, catalog_path).stdout, "ok\n")
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog_path = _put_catalog(repo)
            repo.put(repo.user, _roles_file("full", [_seat("max")]))
            repo.put(repo.project, {"mode": "light", "roles": {}})
            bad = _write(repo, catalog_path, "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max")
            self.assertEqual(bad.returncode, 2, bad.stderr)
            self.assertIn("variant=none", bad.stderr)
            forced = _write(repo, catalog_path, "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max", "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            refused = _validate(repo, catalog_path)
            self.assertEqual(refused.returncode, 1, refused.stdout)
            self.assertIn("variant=none", refused.stdout)

    def test_config_write_mode_merge_matches_validate(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog_path = _put_catalog(repo)
            custom = repo.directory / "custom-roles.json"
            repo.put(custom, _roles_file("light", [_seat("max")]))
            repo.put(repo.project, {"mode": "full", "roles": {}})
            good = _write(repo, catalog_path, "--config", str(custom), "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max")
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertEqual(json.loads(custom.read_text())["mode"], "light")
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog_path = _put_catalog(repo)
            custom = repo.directory / "custom-roles.json"
            repo.put(custom, _roles_file("full", [_seat("max")]))
            repo.put(repo.project, {"mode": "light", "roles": {}})
            bad = _write(repo, catalog_path, "--config", str(custom), "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max")
            self.assertEqual(bad.returncode, 2, bad.stderr)
            self.assertIn("variant=none", bad.stderr)

    def test_project_write_budget_merge_matches_validate(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog_path = _put_catalog(repo)
            repo.put(repo.user, {"mode": "light", "budget": "unlimited", "roles": {}})
            repo.put(repo.project, _roles_file(None, [_seat("max")], budget=None))
            good = _write(repo, catalog_path, "--project", "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max")
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertEqual(_validate(repo, catalog_path).stdout, "ok\n")
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog_path = _put_catalog(repo)
            repo.put(repo.user, {"mode": "light", "budget": "default", "roles": {}})
            repo.put(repo.project, _roles_file(None, [_seat("max")], budget=None))
            bad = _write(repo, catalog_path, "--project", "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max")
            self.assertEqual(bad.returncode, 2, bad.stderr)
            self.assertIn("variant=none", bad.stderr)

    def test_user_write_budget_merge_matches_validate(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog_path = _put_catalog(repo)
            repo.put(repo.user, _roles_file(None, [_seat("max")], budget="default"))
            repo.put(repo.project, {"budget": "unlimited"})
            good = _write(repo, catalog_path, "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max")
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertEqual(_validate(repo, catalog_path).stdout, "ok\n")
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog_path = _put_catalog(repo)
            repo.put(repo.user, _roles_file("light", [_seat("max")], budget="default"))
            repo.put(repo.project, {"budget": "default"})
            bad = _write(repo, catalog_path, "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max")
            self.assertEqual(bad.returncode, 2, bad.stderr)
            self.assertIn("variant=none", bad.stderr)

    def test_inherit_allow_ownership_matches_validate(self):
        go_catalog = {"providers": [{
            "providerInstanceId": "opencode",
            "driverKind": "opencode",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "opencode/allowed-model", "options": []}, {"id": "opencode/metered-model", "options": []}],
        }]}
        parent = "opencode/excluded-model"
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, go_catalog)
            repo.put(repo.user, {
                "version": 1, "mode": "full", "budget": "default",
                "roles": {"bug-fix": ["inherit"]},
                "exclude": [{"model": "excluded-model"}],
            })
            repo.put(repo.project, {
                "version": 1, "mode": "full", "budget": "default",
                "roles": {"bug-fix": ["inherit"]},
                "exclude": [{"model": "excluded-model"}],
                "opencodeAllow": ["opencode/allowed-model"],
            })
            good = repo.run("write", "--catalog", str(path), "--parent", parent,
                            "--set", "bug-fix=inherit")
            self.assertEqual(good.returncode, 0, good.stderr)
            validated = repo.run("validate", "--catalog", str(path), "--parent", parent)
            self.assertEqual(validated.returncode, 0, validated.stdout)
            self.assertEqual(validated.stdout, "ok\n")
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, go_catalog)
            repo.put(repo.user, {
                "version": 1, "mode": "full", "budget": "default",
                "roles": {"bug-fix": ["inherit"]},
                "exclude": [{"model": "excluded-model"}],
                "opencodeAllow": ["opencode/allowed-model"],
            })
            repo.put(repo.project, {
                "version": 1, "mode": "full", "budget": "default",
                "roles": {"bug-fix": ["inherit"]},
                "exclude": [{"model": "excluded-model"}],
            })
            bad = repo.run("write", "--catalog", str(path), "--parent", parent,
                           "--project", "--set", "bug-fix=inherit")
            self.assertEqual(bad.returncode, 2, bad.stderr)
            self.assertIn("allow list", bad.stderr)

    def test_project_symlink_replace_models_directory_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog_path = _put_catalog(repo)
            repo.put(repo.user, _roles_file("full", [_seat("max")]))
            repo.project.parent.mkdir(parents=True, exist_ok=True)
            repo.project.symlink_to(repo.user)
            before = repo.user.read_bytes()
            good = _write(repo, catalog_path, "--project", "--replace",
                          "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max")
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertEqual(repo.user.read_bytes(), before)
            self.assertFalse(repo.project.is_symlink())
            self.assertEqual(_validate(repo, catalog_path).stdout, "ok\n")
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog_path = _put_catalog(repo)
            repo.put(repo.user, _roles_file("light", [_seat("max")]))
            repo.project.parent.mkdir(parents=True, exist_ok=True)
            repo.project.symlink_to(repo.user)
            bad = _write(repo, catalog_path, "--project", "--replace",
                         "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max")
            self.assertEqual(bad.returncode, 2, bad.stderr)
            self.assertIn("variant=none", bad.stderr)

    def test_light_write_through_symlink_parent_chain_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, _roles_file("full", [_seat("max")]))
            middle = repo.directory / "middle"
            (middle / "child").mkdir(parents=True)
            repo.project.parent.mkdir(parents=True, exist_ok=True)
            (repo.project.parent / "jump").symlink_to(middle / "child")
            (middle / repo.project.name).symlink_to(repo.user)
            try:
                repo.project.unlink()
            except FileNotFoundError:
                pass
            repo.project.symlink_to(f"jump/../{repo.project.name}")
            catalog_path = _put_catalog(repo)
            bad = _write(repo, catalog_path, "--mode", "light")
            self.assertEqual(bad.returncode, 2, bad.stderr)
            self.assertIn("variant=none", bad.stderr)
            forced = _write(repo, catalog_path, "--mode", "light", "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            refused = _validate(repo, catalog_path)
            self.assertEqual(refused.returncode, 1, refused.stdout)
            self.assertIn("variant=none", refused.stdout)

    def test_full_mode_succeeds_and_light_mode_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog_path = _put_catalog(repo)
            repo.put(repo.user, _roles_file("full", [_seat("max")]))
            good = _write(repo, catalog_path, "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max")
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertEqual(_validate(repo, catalog_path).stdout, "ok\n")
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog_path = _put_catalog(repo)
            repo.put(repo.user, _roles_file("light", [_seat("max")]))
            bad = _write(repo, catalog_path, "--set", f"bug-fix={PROVIDER}/{MODEL}?variant=max")
            self.assertEqual(bad.returncode, 2, bad.stderr)
            self.assertIn("variant=none", bad.stderr)


if __name__ == "__main__":
    unittest.main()
