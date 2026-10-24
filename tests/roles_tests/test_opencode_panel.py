if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import os
import tempfile
from tests import in_process
from .support import NO_PARENT_INFO, ROOT, Repo, setUpModule
from .catalogs import LIMIT, OPUS_BACKUP, PANEL_RULE, metered_line, refusal
from .test_opencode import DEEPSEEK, GO_STEP, OpenCodeAllowCliCase, PICKLE, SPACE_BUNNY, bare_seat, catalog_model, driver_kind_provider, dumped, relaunch_bug_fix, shown_bug_fix, user_document


def verifier_panel(notes=None):
    payload = {
        "decision": "panel",
        "role": "verifiers",
        "failed": "codex/gpt-6.1-sol",
        "seats": [bare_seat("opencode", PICKLE), bare_seat("opencode-go", GO_STEP)],
        "rule": PANEL_RULE,
    }
    if notes is not None:
        payload["notes"] = notes
    payload["report"] = (
        "verifiers: codex/gpt-6.1-sol hit its usage limit; every paid reviewer backup is out, "
        "so review backups runs 2 seats; land only if no reviewer reproduces a blocker and at least two pass"
    )
    return payload


class OpenCodeAllowCliTest(OpenCodeAllowCliCase):
    def open_panel_catalog(self, directory, extra=()):
        repo = Repo(directory)
        path = repo.directory / "catalog.json"
        repo.put(path, {"providers": [
            {
                "providerInstanceId": "opencode",
                "driverKind": "opencode",
                "canRunChildTask": True,
                "models": [catalog_model(PICKLE)],
            },
            {
                "providerInstanceId": "opencode-go",
                "driverKind": "opencode",
                "canRunChildTask": True,
                "models": [catalog_model(GO_STEP)],
            },
            *extra,
        ]})
        return repo, str(path)


    def backup_verifiers(self, repo, catalog):
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=(
                "backup",
                "--cwd", str(repo.directory), "--catalog", catalog,
                "--role", "verifiers", "--provider", "codex", "--model", "gpt-6.1-sol",
                "--author", "claudeAgent/claude-opus-5-5",
                "--out", "grok", "--out", "claudeAgent",
                "--brief-mode", "light",
            ),
            env={**os.environ, "XDG_CONFIG_HOME": str(repo.directory)},
            stdin=LIMIT,
        ))


    def test_readers_note_a_metered_opencode_seat_and_do_not_rewrite(self):
        line = metered_line("bug-fix", "opencode", PICKLE)
        seat = bare_seat("opencode", PICKLE)
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE), catalog_model(SPACE_BUNNY)])
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": [seat]}, "backups": {"bug-fix": OPUS_BACKUP}})
            before = repo.user.read_bytes()
            shown = repo.run("show", "--catalog", catalog, "--role", "bug-fix")
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertEqual(shown.stderr, NO_PARENT_INFO)
            self.assertEqual(shown.stdout, dumped(shown_bug_fix(str(repo.user), seat, [line])))
            self.assertEqual(repo.user.read_bytes(), before)
            backed = self.backup_bug_fix(repo, catalog)
            self.assertEqual(backed.returncode, 0, backed.stderr)
            self.assertEqual(backed.stderr, "")
            self.assertEqual(backed.stdout, dumped(relaunch_bug_fix([line])))
            self.assertEqual(repo.user.read_bytes(), before)
            validated = repo.run("validate", "--catalog", catalog)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(validated.stderr, "")
            self.assertEqual(validated.stdout, line + "\nok\n")
            self.assertEqual(repo.user.read_bytes(), before)
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": [bare_seat("grok", "grok-4.7")]}, "backups": {"bug-fix": OPUS_BACKUP}})
            grok_before = repo.user.read_bytes()
            grok_backup = self.backup_bug_fix(repo, catalog)
            self.assertEqual(grok_backup.returncode, 0, grok_backup.stderr)
            self.assertEqual(grok_backup.stderr, "")
            self.assertEqual(grok_backup.stdout, dumped(relaunch_bug_fix()))
            self.assertNotIn("\n  \"notes\"", grok_backup.stdout)
            self.assertEqual(repo.user.read_bytes(), grok_before)
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": [seat]}})
            repo.put(repo.project, {"version": 1, "opencodeAllow": [PICKLE]})
            user_noted = repo.run("show", "--catalog", catalog, "--role", "bug-fix")
            self.assertEqual(user_noted.returncode, 0, user_noted.stderr)
            self.assertEqual(user_noted.stdout, dumped(shown_bug_fix(str(repo.user), seat, [line])))
            repo.put(repo.user, {"version": 1, "roles": {}})
            repo.put(repo.project, {
                "version": 1,
                "roles": {"bug-fix": [seat]},
                "opencodeAllow": [PICKLE],
            })
            project_silent = repo.run("show", "--catalog", catalog, "--role", "bug-fix")
            self.assertEqual(project_silent.returncode, 0, project_silent.stderr)
            self.assertEqual(project_silent.stdout, dumped(shown_bug_fix(str(repo.project), seat)))


    def test_missing_opencode_allow_loads_as_an_empty_list(self):
        line = metered_line("bug-fix", "opencode", PICKLE)
        seat = bare_seat("opencode", PICKLE)
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": [seat]}, "kept": "old"})
            shown = repo.run("show", "--catalog", catalog, "--role", "bug-fix")
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertEqual(shown.stderr, NO_PARENT_INFO)
            self.assertEqual(shown.stdout, dumped(shown_bug_fix(str(repo.user), seat, [line])))


    def test_non_opencode_write_and_show_stay_the_same(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            for model_id in ("grok-4.7", "grok-opencode-1"):
                with self.subTest(model=model_id):
                    completed = self.write_bug_fix(repo, catalog, "grok", model_id)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, f"wrote {repo.user}\n")
                    self.assertEqual(completed.stderr, "")
                    self.assertEqual(repo.user.read_text(), dumped(user_document({
                        "bug-fix": [bare_seat("grok", model_id)],
                    })))
                    self.assertNotIn("opencodeAllow", repo.user.read_text())
                    shown = repo.run("show", "--catalog", catalog, "--role", "bug-fix")
                    self.assertEqual(shown.returncode, 0, shown.stderr)
                    self.assertEqual(shown.stderr, NO_PARENT_INFO)
                    self.assertEqual(shown.stdout, dumped(shown_bug_fix(
                        str(repo.user), bare_seat("grok", model_id), mode_source=str(repo.user),
                    )))
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [driver_kind_provider("opencode", "muse", PICKLE)]})
            catalog = str(path)
            completed = self.write_bug_fix(repo, catalog, "opencode", PICKLE)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, f"wrote {repo.user}\n")
            self.assertEqual(completed.stderr, "")
            self.assertEqual(repo.user.read_text(), dumped(user_document({
                "bug-fix": [bare_seat("opencode", PICKLE)],
            })))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
            shown = repo.run("show", "--catalog", catalog, "--role", "bug-fix")
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertEqual(shown.stderr, NO_PARENT_INFO)
            self.assertEqual(shown.stdout, dumped(shown_bug_fix(
                str(repo.user), bare_seat("opencode", PICKLE), mode_source=str(repo.user),
            )))


    def test_an_unlisted_metered_panel_gets_the_allow_notes(self):
        seats = [bare_seat("opencode", PICKLE), bare_seat("opencode-go", GO_STEP)]
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.open_panel_catalog(directory)
            repo.put(repo.user, {"version": 1, "roles": {"review backups": seats}})
            repo.put(repo.project, {
                "version": 1,
                "roles": {"verifiers": seats},
                "opencodeAllow": [PICKLE, GO_STEP],
            })
            user_before = repo.user.read_bytes()
            project_before = repo.project.read_bytes()
            completed = self.backup_verifiers(repo, catalog)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(completed.stdout, dumped(verifier_panel([
                metered_line("review backups", "opencode", PICKLE),
                metered_line("review backups", "opencode-go", GO_STEP),
            ])))
            self.assertEqual(repo.user.read_bytes(), user_before)
            self.assertEqual(repo.project.read_bytes(), project_before)


    def test_a_panel_allowed_by_the_file_that_supplied_it_gets_no_notes(self):
        seats = [bare_seat("opencode", PICKLE), bare_seat("opencode-go", GO_STEP)]
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.open_panel_catalog(directory)
            repo.put(repo.user, {
                "version": 1,
                "roles": {"review backups": seats},
                "opencodeAllow": [PICKLE, GO_STEP],
            })
            repo.put(repo.project, {"version": 1, "roles": {"verifiers": ["inherit"]}})
            user_before = repo.user.read_bytes()
            project_before = repo.project.read_bytes()
            completed = self.backup_verifiers(repo, catalog)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(completed.stdout, dumped(verifier_panel()))
            self.assertNotIn("\n  \"notes\"", completed.stdout)
            self.assertEqual(repo.user.read_bytes(), user_before)
            self.assertEqual(repo.project.read_bytes(), project_before)


    def test_a_worker_backup_does_not_gain_panel_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.open_panel_catalog(directory, (
                {
                    "providerInstanceId": "claudeAgent",
                    "canRunChildTask": True,
                    "models": [catalog_model("claude-opus-5-5")],
                },
                {
                    "providerInstanceId": "codex",
                    "canRunChildTask": True,
                    "models": [catalog_model("gpt-6.1-sol")],
                },
            ))
            repo.put(repo.user, {
                "version": 1,
                "roles": {
                    "review backups": [
                        bare_seat("opencode", PICKLE),
                        bare_seat("opencode-go", GO_STEP),
                    ],
                    "bug-fix": [bare_seat("grok", "grok-4.7")],
                },
                "backups": {"bug-fix": OPUS_BACKUP},
            })
            user_before = repo.user.read_bytes()
            self.assertFalse(repo.project.exists())
            completed = self.backup_bug_fix(repo, catalog)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(completed.stdout, dumped(relaunch_bug_fix()))
            self.assertEqual(repo.user.read_bytes(), user_before)
            self.assertFalse(repo.project.exists())


    def test_write_refuses_opencode_go_when_driver_kind_is_missing(self):
        provider = {
            "providerInstanceId": "opencode-go",
            "canRunChildTask": True,
            "models": [catalog_model(DEEPSEEK)],
        }
        seat = f"bug-fix=opencode-go/{DEEPSEEK}"
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [provider]})
            catalog = str(path)
            refused = self.write_bug_fix(repo, catalog, "opencode-go", DEEPSEEK)
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertEqual(refused.stderr, refusal("bug-fix", "opencode-go", DEEPSEEK))
            self.assertFalse(repo.user.exists())
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [provider]})
            catalog = str(path)
            refused = repo.run("write", "--project", "--catalog", catalog, "--set", seat)
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertEqual(refused.stderr, refusal("bug-fix", "opencode-go", DEEPSEEK))
            self.assertFalse(repo.project.exists())
            self.assertFalse(repo.user.exists())
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [provider]})
            catalog = str(path)
            seeded = self.write_bug_fix(repo, catalog, "opencode-go", DEEPSEEK, "--force")
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            self.assertEqual(repo.user.read_text(), dumped(user_document({
                "bug-fix": [bare_seat("opencode-go", DEEPSEEK)],
            })))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
            before = repo.user.read_bytes()
            kept = repo.run("write", "--catalog", catalog, "--keep", "--budget", "small")
            self.assertEqual(kept.returncode, 2)
            self.assertEqual(kept.stdout, "")
            self.assertEqual(kept.stderr, refusal("bug-fix", "opencode-go", DEEPSEEK))
            self.assertEqual(repo.user.read_bytes(), before)
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [provider]})
            catalog = str(path)
            seeded = repo.run("write", "--project", "--catalog", catalog, "--set", seat, "--force")
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            self.assertEqual(repo.project.read_text(), dumped({
                "version": 1,
                "roles": {"bug-fix": [bare_seat("opencode-go", DEEPSEEK)]},
            }))
            self.assertNotIn("opencodeAllow", repo.project.read_text())
            before = repo.project.read_bytes()
            kept = repo.run(
                "write", "--project", "--catalog", catalog, "--keep", "--budget", "small",
            )
            self.assertEqual(kept.returncode, 2)
            self.assertEqual(kept.stdout, "")
            self.assertEqual(kept.stderr, refusal("bug-fix", "opencode-go", DEEPSEEK))
            self.assertEqual(repo.project.read_bytes(), before)
            self.assertFalse(repo.user.exists())


    def test_a_missing_driver_kind_on_another_id_writes_without_an_allow_list(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [{
                "providerInstanceId": "other",
                "canRunChildTask": True,
                "models": [catalog_model("other/paid")],
            }]})
            completed = self.write_bug_fix(repo, str(path), "other", "other/paid")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, f"wrote {repo.user}\n")
            self.assertEqual(completed.stderr, "")
            self.assertEqual(repo.user.read_text(), dumped(user_document({
                "bug-fix": [bare_seat("other", "other/paid")],
            })))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [{
                "providerInstanceId": "opencode-go",
                "driverKind": "grok",
                "canRunChildTask": True,
                "models": [catalog_model(DEEPSEEK)],
            }]})
            completed = self.write_bug_fix(repo, str(path), "opencode-go", DEEPSEEK)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, f"wrote {repo.user}\n")
            self.assertEqual(completed.stderr, "")
            self.assertEqual(repo.user.read_text(), dumped(user_document({
                "bug-fix": [bare_seat("opencode-go", DEEPSEEK)],
            })))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
