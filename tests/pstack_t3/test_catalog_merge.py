if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

import io
import json
import tempfile
from contextlib import redirect_stderr, redirect_stdout

import roles  # noqa: E402
from tests.pstack_t3.support import config  # noqa: E402

NEW_MODEL = "gpt-6.5-nova"


def fresh_catalog(directory):
    path = Path(directory) / "catalog.json"
    path.write_text((ROOT / "tests/fixtures/catalog.json").read_text())
    return path


def run_merge(argv, stdin_text=None):
    out, err = io.StringIO(), io.StringIO()
    if stdin_text is None:
        with redirect_stdout(out), redirect_stderr(err):
            code = roles.main(argv)
    else:
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(stdin_text)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = roles.main(argv)
        finally:
            sys.stdin = old_stdin
    return code, out.getvalue(), err.getvalue()


def search_result(entries):
    return {
        "providers": [
            {
                "providerInstanceId": provider_id,
                "models": [
                    {"id": model_id, "label": label, "options": options}
                    for model_id, label, options in models
                ],
            }
            for provider_id, models in entries
        ]
    }


class CatalogMergeTest(unittest.TestCase):
    def test_merge_adds_a_model_the_saved_catalog_lacks(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = fresh_catalog(directory)
            result = search_result([
                ("codex", [(NEW_MODEL, "GPT-6.5-Nova", [{"id": "reasoningEffort"}])]),
            ])
            code, out, err = run_merge(
                ["catalog", "merge", "--catalog", str(catalog), "--from", "-"],
                json.dumps(result),
            )
            self.assertEqual(code, 0, err)
            self.assertIn(f"added codex/{NEW_MODEL}", out)
            self.assertIn(f"wrote {catalog}", out)
            codex = next(
                item for item in json.loads(catalog.read_text())["providers"]
                if item["providerInstanceId"] == "codex"
            )
            model = next(item for item in codex["models"] if item["id"] == NEW_MODEL)
            self.assertEqual(model["options"], [{"id": "reasoningEffort"}])

    def test_merge_keeps_a_saved_entry_and_its_options_for_a_model_it_has(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = fresh_catalog(directory)
            before = catalog.read_bytes()
            result = search_result([
                ("codex", [("gpt-6.1-sol", "Different Label", [])]),
            ])
            code, out, err = run_merge(
                ["catalog", "merge", "--catalog", str(catalog), "--from", "-"],
                json.dumps(result),
            )
            self.assertEqual(code, 0, err)
            self.assertIn("nothing to add", out)
            self.assertEqual(catalog.read_bytes(), before)
            codex = next(
                item for item in json.loads(catalog.read_text())["providers"]
                if item["providerInstanceId"] == "codex"
            )
            self.assertEqual(
                [item["id"] for item in codex["models"]], ["gpt-6.1-sol", "gpt-6-luna"]
            )
            saved = next(item for item in codex["models"] if item["id"] == "gpt-6.1-sol")
            self.assertEqual(saved["label"], "GPT-6.1-Sol")
            self.assertTrue(saved["options"])

    def test_merge_refuses_a_provider_the_saved_catalog_lacks_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = fresh_catalog(directory)
            before = catalog.read_bytes()
            result = search_result([("nobody", [("x-1", "X-1", [])])])
            path = Path(directory) / "search.json"
            path.write_text(json.dumps(result))
            code, out, err = run_merge(
                ["catalog", "merge", "--catalog", str(catalog), "--from", str(path)]
            )
            self.assertEqual(code, 2)
            self.assertIn("search result provider nobody is not in the saved catalog", err)
            self.assertEqual(catalog.read_bytes(), before)

    def test_merge_refuses_a_provider_the_saved_catalog_cannot_run(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = fresh_catalog(directory)
            before = catalog.read_bytes()
            result = search_result([("cursor", [("cursor-new", "Cursor New", [])])])
            code, out, err = run_merge(
                ["catalog", "merge", "--catalog", str(catalog), "--from", "-"],
                json.dumps(result),
            )
            self.assertEqual(code, 2)
            self.assertIn("not runnable in the saved catalog", err)
            self.assertEqual(catalog.read_bytes(), before)

    def test_merge_refuses_a_result_that_names_no_model(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = fresh_catalog(directory)
            before = catalog.read_bytes()
            result = search_result([("codex", []), ("grok", [])])
            code, out, err = run_merge(
                ["catalog", "merge", "--catalog", str(catalog), "--from", "-"],
                json.dumps(result),
            )
            self.assertEqual(code, 2)
            self.assertIn("search result names no model to merge", err)
            self.assertEqual(catalog.read_bytes(), before)

    def test_empty_model_providers_beside_a_match_merge_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = fresh_catalog(directory)
            result = search_result([
                ("claudeAgent", []),
                ("grok", [("grok-5", "Grok 5", [])]),
            ])
            code, out, err = run_merge(
                ["catalog", "merge", "--catalog", str(catalog), "--from", "-"],
                json.dumps(result),
            )
            self.assertEqual(code, 0, err)
            self.assertIn("added grok/grok-5", out)
            self.assertNotIn("added claudeAgent", out)
            saved = json.loads(catalog.read_text())
            grok = next(item for item in saved["providers"] if item["providerInstanceId"] == "grok")
            self.assertEqual([item["id"] for item in grok["models"]], ["grok-4.7", "grok-5"])
            claude = next(
                item for item in saved["providers"] if item["providerInstanceId"] == "claudeAgent"
            )
            self.assertEqual(len(claude["models"]), 3)

    def test_write_accepts_the_merged_model_and_saves_it_as_the_snapshot(self):
        user_path = roles.user_config_path()
        snapshot = roles.snapshot_path()
        old_user = user_path.read_bytes() if user_path.is_file() else None
        old_snapshot = snapshot.read_bytes() if snapshot.is_file() else None

        def restore():
            if old_user is None:
                user_path.unlink(missing_ok=True)
            else:
                user_path.write_bytes(old_user)
            if old_snapshot is None:
                snapshot.unlink(missing_ok=True)
            else:
                snapshot.write_bytes(old_snapshot)

        self.addCleanup(restore)
        with tempfile.TemporaryDirectory() as directory:
            catalog = fresh_catalog(directory)
            write_args = [
                "write", "--cwd", directory, "--catalog", str(catalog),
                "--set", f"bug-fix=codex/{NEW_MODEL}",
            ]
            self.assertEqual(run_merge(write_args)[0], 2)
            result = search_result([
                ("codex", [(NEW_MODEL, "GPT-6.5-Nova", [])]),
            ])
            search_path = Path(directory) / "search.json"
            search_path.write_text(json.dumps(result))
            code, out, err = run_merge(
                ["catalog", "merge", "--catalog", str(catalog), "--from", str(search_path)]
            )
            self.assertEqual(code, 0, err)
            code, out, err = run_merge(write_args)
            self.assertEqual(code, 0, err)
            stored = json.loads(user_path.read_text())
            self.assertEqual(
                stored["roles"]["bug-fix"],
                [{"providerInstanceId": "codex", "model": NEW_MODEL}],
            )
            snapshotted = json.loads(snapshot.read_text())
            codex = next(
                item for item in snapshotted["providers"]
                if item["providerInstanceId"] == "codex"
            )
            self.assertIn(NEW_MODEL, [item["id"] for item in codex["models"]])

    def test_search_hint_names_a_configured_model_the_short_list_left_out(self):
        base = {
            "providerInstanceId": "codex",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "gpt-6.1-sol", "options": []}],
        }
        catalog = {"providers": [{**base, "omittedModels": 3}]}
        seat = {"providerInstanceId": "codex", "model": "gone"}
        entry = roles.resolve(config(**{"bug-fix": [seat]}), catalog, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["search"], ["codex/gone"])
        short = {"providers": [dict(base)]}
        plain = roles.resolve(config(**{"bug-fix": [seat]}), short, ["bug-fix"])["roles"]["bug-fix"]
        self.assertNotIn("search", plain)


if __name__ == "__main__":
    unittest.main()
