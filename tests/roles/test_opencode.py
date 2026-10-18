import json
import os
import tempfile
import unittest
from tests import in_process
from .support import ROOT, Repo, setUpModule
from .catalogs import LIMIT, refusal


class OpenCodeAllowCliCase(unittest.TestCase):
    def prepare(self, directory, models, extra=()):
        repo = Repo(directory)
        path = repo.directory / "catalog.json"
        repo.put(path, {"providers": [
            {
                "providerInstanceId": "opencode",
                "canRunChildTask": True,
                "constraints": [],
                "models": models,
            },
            {
                "providerInstanceId": "claudeAgent",
                "canRunChildTask": True,
                "constraints": [],
                "models": [{"id": "claude-opus-5-5", "options": []}],
            },
            {
                "providerInstanceId": "codex",
                "canRunChildTask": True,
                "constraints": [],
                "models": [{"id": "gpt-6.1-sol", "options": []}],
            },
            {
                "providerInstanceId": "grok",
                "canRunChildTask": True,
                "constraints": [],
                "models": [
                    {"id": "grok-4.7", "options": []},
                    {"id": "grok-opencode-1", "options": []},
                ],
            },
            *extra,
        ]})
        return repo, str(path)


    def write_bug_fix(self, repo, catalog, provider, model, *flags):
        return repo.run("write", "--catalog", catalog, "--set", f"bug-fix={provider}/{model}", *flags)


    def backup_bug_fix(self, repo, catalog):
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=(
                "backup",
                "--cwd", str(repo.directory), "--catalog", catalog,
                "--role", "bug-fix", "--provider", "codex", "--model", "gpt-6.1-sol",
            ),
            env={**os.environ, "XDG_CONFIG_HOME": str(repo.directory)},
            stdin=LIMIT,
        ))


PICKLE = "opencode/big-pickle"


SPACE_BUNNY = "opencode/space-bunny-free"


GO_STEP = "opencode-go/step-5-preview-free"


DEEPSEEK = "opencode-go/deepseek-v4.1-flash"


def catalog_model(model_id, **fields):
    return {"id": model_id, "options": [], **fields}


def driver_kind_provider(provider_id, driver, model_id, **fields):
    return {
        "providerInstanceId": provider_id,
        "driverKind": driver,
        "canRunChildTask": True,
        "constraints": [],
        "models": [catalog_model(model_id, **fields)],
    }


def bare_seat(provider, model):
    return {"providerInstanceId": provider, "model": model}


def dumped(payload):
    return json.dumps(payload, indent=2) + "\n"


def user_document(roles, allow=None, budget="default"):
    document = {"version": 1, "roles": roles, "budget": budget, "mode": "light"}
    if allow is not None:
        document["opencodeAllow"] = allow
    return document


def shown_bug_fix(source, seat, notes=None, mode_source="default"):
    entry = {"source": source, "seats": [seat]}
    if notes is not None:
        entry["notes"] = notes
    return {
        "budget": "default",
        "mode": "light",
        "modeSource": mode_source,
        "escalate": None,
        "catalog": True,
        "roles": {"bug-fix": entry},
    }


def relaunch_bug_fix(notes=None):
    payload = {
        "decision": "relaunch",
        "role": "bug-fix",
        "failed": "codex/gpt-6.1-sol",
        "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
    }
    if notes is not None:
        payload["notes"] = notes
    payload["report"] = (
        "bug-fix: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5"
    )
    return payload


class OpenCodeAllowCliTest(OpenCodeAllowCliCase):
    def test_write_refuses_unnamed_metered_opencode_seat(self):
        both = [catalog_model(PICKLE), catalog_model(SPACE_BUNNY)]
        cases = (
            ("opencode", PICKLE, both, ()),
            ("opencode", SPACE_BUNNY, both, ()),
            ("muse", "muse-spark-1", [catalog_model(PICKLE)], (driver_kind_provider("muse", "opencode", "muse-spark-1"),)),
        )
        for provider, model, models, extra in cases:
            with self.subTest(provider=provider, model=model):
                with tempfile.TemporaryDirectory() as directory:
                    repo, catalog = self.prepare(directory, models, extra)
                    absent = self.write_bug_fix(repo, catalog, provider, model)
                    self.assertEqual(absent.returncode, 2)
                    self.assertEqual(absent.stdout, "")
                    self.assertEqual(absent.stderr, refusal("bug-fix", provider, model))
                    self.assertFalse(repo.user.exists())
                    prior = b'{"version": 1, "roles": {}}\n'
                    repo.user.parent.mkdir(parents=True, exist_ok=True)
                    repo.user.write_bytes(prior)
                    unchanged = self.write_bug_fix(repo, catalog, provider, model)
                    self.assertEqual(unchanged.returncode, 2)
                    self.assertEqual(unchanged.stdout, "")
                    self.assertEqual(unchanged.stderr, refusal("bug-fix", provider, model))
                    self.assertEqual(repo.user.read_bytes(), prior)
                    forced = self.write_bug_fix(repo, catalog, provider, model, "--force")
                    self.assertEqual(forced.returncode, 0, forced.stderr)
                    self.assertEqual(forced.stdout, f"wrote {repo.user}\n")
                    self.assertEqual(forced.stderr, "")
                    self.assertEqual(repo.user.read_text(), dumped(user_document({
                        "bug-fix": [bare_seat(provider, model)],
                    })))
                    self.assertNotIn("opencodeAllow", repo.user.read_text())


    def test_write_accepts_catalog_free_opencode_model_without_an_allow_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [
                catalog_model(PICKLE, cost=0),
                catalog_model(SPACE_BUNNY, cost=0),
            ])
            completed = repo.run(
                "write", "--catalog", catalog, "--set",
                f"verifiers=opencode/{PICKLE};opencode/{SPACE_BUNNY}",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, f"wrote {repo.user}\n")
            self.assertEqual(completed.stderr, "")
            self.assertEqual(repo.user.read_text(), dumped(user_document({
                "verifiers": [bare_seat("opencode", PICKLE), bare_seat("opencode", SPACE_BUNNY)],
            })))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
        accepted = (
            ("float zero", catalog_model(PICKLE, cost=0.0)),
            ("pricing free", catalog_model(PICKLE, pricing="free")),
            ("every present marker free", catalog_model(PICKLE, cost=0, pricing="free", tier="free")),
        )
        for label, entry in accepted:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    repo, catalog = self.prepare(directory, [entry])
                    completed = self.write_bug_fix(repo, catalog, "opencode", PICKLE)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, f"wrote {repo.user}\n")
                    self.assertEqual(completed.stderr, "")
                    self.assertEqual(repo.user.read_text(), dumped(user_document({
                        "bug-fix": [bare_seat("opencode", PICKLE)],
                    })))
                    self.assertNotIn("opencodeAllow", repo.user.read_text())
        refused = (
            ("paid tier", catalog_model(PICKLE, cost=0, tier="paid")),
            ("string zero", catalog_model(PICKLE, cost="0")),
            ("capital free", catalog_model(PICKLE, cost="Free")),
            ("false", catalog_model(PICKLE, cost=False)),
        )
        for label, entry in refused:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    repo, catalog = self.prepare(directory, [entry])
                    completed = self.write_bug_fix(repo, catalog, "opencode", PICKLE)
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr, refusal("bug-fix", "opencode", PICKLE))
                    self.assertFalse(repo.user.exists())


    def test_allow_list_changes_only_through_allow_flags(self):
        completed = in_process.finished(in_process.Reason.HELP, in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=("write", "--help"),
            env={**os.environ, "COLUMNS": "200"},
        ))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        self.assertIn(
            "  --allow-opencode ALLOW_OPENCODE\n"
            + (" " * 24)
            + "Add this full OpenCode model id to the allow list in the file being written.\n",
            completed.stdout,
        )
        self.assertIn(
            "  --deny-opencode DENY_OPENCODE\n"
            + (" " * 24)
            + "Remove this full OpenCode model id from the allow list in the file being written.\n",
            completed.stdout,
        )
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE), catalog_model(SPACE_BUNNY)])
            added = repo.run(
                "write", "--catalog", catalog,
                "--set", f"verifiers=opencode/{PICKLE}",
                "--allow-opencode", SPACE_BUNNY,
                "--allow-opencode", PICKLE,
                "--allow-opencode", PICKLE,
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            self.assertEqual(added.stdout, f"wrote {repo.user}\n")
            self.assertEqual(added.stderr, "")
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"verifiers": [bare_seat("opencode", PICKLE)]},
                [PICKLE, SPACE_BUNNY],
            )))
            dropped = repo.run("write", "--catalog", catalog, "--replace", "--set", "bug-fix=grok/grok-4.7")
            self.assertEqual(dropped.returncode, 0, dropped.stderr)
            self.assertEqual(dropped.stdout, f"wrote {repo.user}\n")
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                [PICKLE, SPACE_BUNNY],
            )))
            denied = repo.run("write", "--catalog", catalog, "--keep", "--deny-opencode", SPACE_BUNNY)
            self.assertEqual(denied.returncode, 0, denied.stderr)
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                [PICKLE],
            )))
            kept = repo.run("write", "--catalog", catalog, "--keep", "--budget", "small")
            self.assertEqual(kept.returncode, 0, kept.stderr)
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                [PICKLE],
                budget="small",
            )))
            restored = repo.run(
                "write", "--catalog", catalog, "--keep",
                "--deny-opencode", PICKLE, "--allow-opencode", PICKLE,
            )
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                [PICKLE],
                budget="small",
            )))
            cleared = repo.run("write", "--catalog", catalog, "--keep", "--deny-opencode", PICKLE)
            self.assertEqual(cleared.returncode, 0, cleared.stderr)
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                budget="small",
            )))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            repo.put(repo.user, {
                "version": 1,
                "roles": {"nope": ["inherit"]},
                "opencodeAllow": [SPACE_BUNNY, PICKLE, PICKLE],
            })
            copied = repo.run("write", "--catalog", catalog, "--replace", "--set", "bug-fix=grok/grok-4.7")
            self.assertEqual(copied.returncode, 0, copied.stderr)
            self.assertEqual(copied.stdout, f"wrote {repo.user}\n")
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                [PICKLE, SPACE_BUNNY],
            )))
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            repo.put(repo.user, {
                "version": 1,
                "roles": {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                "opencodeAllow": ["has space"],
            })
            before = repo.user.read_bytes()
            shape = repo.run("write", "--catalog", catalog, "--set", "bug-fix=grok/grok-4.7")
            self.assertEqual(shape.returncode, 2)
            self.assertEqual(shape.stdout, "")
            self.assertEqual(
                shape.stderr,
                f"error: {repo.user}: opencodeAllow must be a list of non-empty strings with no spaces\n",
            )
            self.assertEqual(repo.user.read_bytes(), before)
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            stored = self.write_bug_fix(repo, catalog, "opencode", PICKLE, "--allow-opencode", PICKLE)
            self.assertEqual(stored.returncode, 0, stored.stderr)
            before = repo.user.read_bytes()
            refused = repo.run("write", "--catalog", catalog, "--keep", "--deny-opencode", PICKLE)
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertEqual(refused.stderr, refusal("bug-fix", "opencode", PICKLE))
            self.assertEqual(repo.user.read_bytes(), before)
            forced = repo.run(
                "write", "--catalog", catalog, "--keep", "--deny-opencode", PICKLE, "--force",
            )
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertEqual(forced.stdout, f"wrote {repo.user}\n")
            self.assertEqual(forced.stderr, "")
            self.assertEqual(repo.user.read_text(), dumped(user_document({
                "bug-fix": [bare_seat("opencode", PICKLE)],
            })))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            repo.put(repo.project, {"version": 1, "roles": {}, "opencodeAllow": [PICKLE]})
            project_bytes = repo.project.read_bytes()
            refused = self.write_bug_fix(repo, catalog, "opencode", PICKLE)
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertEqual(refused.stderr, refusal("bug-fix", "opencode", PICKLE))
            self.assertFalse(repo.user.exists())
            self.assertEqual(repo.project.read_bytes(), project_bytes)
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            repo.put(repo.user, {"version": 1, "roles": {}, "opencodeAllow": [PICKLE]})
            user_bytes = repo.user.read_bytes()
            refused = repo.run(
                "write", "--project", "--catalog", catalog, "--set", f"bug-fix=opencode/{PICKLE}",
            )
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertEqual(refused.stderr, refusal("bug-fix", "opencode", PICKLE))
            self.assertFalse(repo.project.exists())
            self.assertEqual(repo.user.read_bytes(), user_bytes)
