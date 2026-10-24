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
from pathlib import Path
from tests import in_process
from .support import CATALOG, GROK_MEDIUM, GROK_SEAT, INHERIT_ENTRY, ModeCliCase, NO_CATALOG_INFO, NO_PARENT_INFO, OPUS_HIGH, OPUS_MEDIUM, OPUS_SEAT, ROOT, Repo, setUpModule
from .test_mode_sources import bug_fix_show


class ModeCliTest(ModeCliCase):
    def test_light_keeps_a_configured_review_backups_seat_at_xhigh(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"review backups": [OPUS_SEAT, GROK_SEAT]}})
            completed = self.show_role(repo, "--brief-mode", "light", "--role", "review backups")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"]["review backups"]["seats"], [OPUS_SEAT, GROK_SEAT])


    def test_brief_mode_light_keeps_a_configured_high_review_backups_seat_at_high(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"review backups": [OPUS_HIGH, GROK_SEAT]}})
            completed = self.show_role(repo, "--brief-mode", "light", "--role", "review backups")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["roles"]["review backups"]["seats"], [OPUS_HIGH, GROK_SEAT])
        self.assertEqual(payload["mode"], "light")
        self.assertEqual(completed.stderr, "")


    def test_light_with_small_budget_caps_a_high_review_backups_seat_at_medium_as_full_does(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"budget": "small", "roles": {"review backups": [OPUS_HIGH, GROK_SEAT]}})
            light = self.show_role(repo, "--brief-mode", "light", "--role", "review backups")
            full = self.show_role(repo, "--brief-mode", "full", "--role", "review backups")
        for completed in (light, full):
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(completed.stdout)["roles"]["review backups"]["seats"], [OPUS_MEDIUM, GROK_MEDIUM]
            )


    def test_light_show_of_every_role_caps_bug_fix_and_keeps_a_high_verifier(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"bug-fix": [OPUS_SEAT], "verifiers": [OPUS_HIGH]}})
            completed = self.show_role(repo, "--brief-mode", "light")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        shown = json.loads(completed.stdout)["roles"]
        self.assertEqual(shown["bug-fix"]["seats"], [OPUS_MEDIUM])
        self.assertEqual(shown["verifiers"]["seats"], [OPUS_HIGH])


    def test_light_verifiers_without_parent_prints_no_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "verifiers")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")


    def test_light_verifiers_without_catalog_prints_no_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--role", "verifiers")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")


    def test_light_review_backups_without_parent_prints_no_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "review backups")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")


    def test_light_review_backups_without_catalog_prints_no_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--role", "review backups")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")


    def test_light_verifiers_and_bug_fix_without_parent_prints_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "verifiers", "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, NO_PARENT_INFO)


    def test_brief_mode_full_keeps_a_configured_verifier(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "light", "roles": {"verifiers": [OPUS_SEAT, GROK_SEAT]}})
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--brief-mode", "full",
                "--role", "verifiers",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "full")
        self.assertEqual(payload["modeSource"], "brief")
        self.assertEqual(payload["roles"]["verifiers"]["seats"], [OPUS_SEAT, GROK_SEAT])


    def test_session_mode_light_over_project_full(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "full", "roles": {"bug-fix": [OPUS_SEAT]}})
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--session-mode", "light",
                "--role", "bug-fix",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "light")
        self.assertEqual(payload["modeSource"], "session")
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [OPUS_MEDIUM])


    def test_session_mode_full_over_project_light_lifts_the_cap(self):
        role = "bug-fix"
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "full", "roles": {role: [OPUS_SEAT]}})
            full = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", role,
            )
            repo.put(repo.project, {"mode": "light", "roles": {role: [OPUS_SEAT]}})
            lifted = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--session-mode", "full",
                "--role", role,
            )
            capped = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", role,
            )
        self.assertEqual(full.returncode, 0, full.stderr)
        self.assertEqual(lifted.returncode, 0, lifted.stderr)
        self.assertEqual(capped.returncode, 0, capped.stderr)
        full_seats = json.loads(full.stdout)["roles"][role]["seats"]
        lifted_payload = json.loads(lifted.stdout)
        capped_seats = json.loads(capped.stdout)["roles"][role]["seats"]
        self.assertEqual((lifted_payload["mode"], lifted_payload["modeSource"]), ("full", "session"))
        self.assertEqual(lifted_payload["roles"][role]["seats"], [OPUS_SEAT])
        self.assertEqual(lifted_payload["roles"][role]["seats"], full_seats)
        self.assertEqual(capped_seats, [OPUS_MEDIUM])
        self.assertNotEqual(capped_seats, lifted_payload["roles"][role]["seats"])


    def test_brief_mode_full_over_session_mode_light(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "full", "roles": {"bug-fix": [OPUS_SEAT]}})
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--brief-mode", "full",
                "--session-mode", "light",
                "--role", "bug-fix",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "full")
        self.assertEqual(payload["modeSource"], "brief")
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [OPUS_SEAT])


    def test_show_keeps_its_keys_under_mode_flags(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--session-mode", "light",
                "--role", "bug-fix",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload), ["budget", "mode", "modeSource", "escalate", "catalog", "roles"])
        self.assertEqual(payload["budget"], "default")


    def test_light_inherit_becomes_explicit_medium(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light", "roles": {"judgment and prose": ["inherit"]}})
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", "judgment and prose",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["judgment and prose"]
        self.assertEqual(entry["seats"], [OPUS_MEDIUM])
        self.assertEqual(
            entry["info"],
            ["inherit made explicit as claudeAgent/claude-opus-5-5 so the small budget applies"],
        )


    def test_light_with_large_budget_keeps_xhigh(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light", "budget": "large"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), bug_fix_show("light", str(repo.user), None, budget="large"))


    def test_full_with_small_budget_keeps_medium(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "full", "budget": "small", "roles": {"bug-fix": [OPUS_SEAT]}})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [OPUS_MEDIUM])
        self.assertEqual(payload["mode"], "full")


    def test_light_without_parent_prints_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, NO_PARENT_INFO)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"], INHERIT_ENTRY)


    def test_light_without_catalog_prints_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, NO_CATALOG_INFO)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"], INHERIT_ENTRY)


    def test_default_without_parent_prints_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, NO_PARENT_INFO)


    def test_light_with_large_budget_without_parent_prints_no_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light", "budget": "large"})
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")


    def test_worktree_reads_the_brief_mode(self):
        with self.open_repo() as directory:
            root = Path(directory)
            main = root / "main"
            (main / ".git").mkdir(parents=True)
            project = main / ".pstack" / "t3-roles.json"
            project.parent.mkdir(parents=True)
            project.write_text(json.dumps({"mode": "full"}) + "\n", encoding="utf-8")
            worktree = main / "wt"
            worktree.mkdir()
            (worktree / ".git").write_text("gitdir: ../.git/worktrees/wt\n", encoding="utf-8")
            env = {**os.environ, "XDG_CONFIG_HOME": str(root)}

            def invoke(*extra):
                return in_process.invoke(in_process.Invocation(
                    program=ROOT / "t3/scripts/roles.py",
                    argv=(
                        "show",
                        "--cwd", str(worktree),
                        "--catalog", str(CATALOG),
                        "--parent", "claudeAgent/claude-opus-5-5",
                        "--role", "bug-fix",
                        *extra,
                    ),
                    env=env,
                ))

            capped = invoke("--brief-mode", "light")
            plain = invoke()
        self.assertEqual(capped.returncode, 0, capped.stderr)
        capped_payload = json.loads(capped.stdout)
        self.assertEqual(capped_payload["mode"], "light")
        self.assertEqual(capped_payload["modeSource"], "brief")
        self.assertEqual(capped_payload["roles"]["bug-fix"], INHERIT_ENTRY)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        plain_payload = json.loads(plain.stdout)
        self.assertEqual(plain_payload["mode"], "light")
        self.assertEqual(plain_payload["modeSource"], "default")
        self.assertEqual(plain_payload["roles"]["bug-fix"], INHERIT_ENTRY)


    def test_bad_brief_mode_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.run("show", "--brief-mode", "turbo", "--role", "bug-fix")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")


    def test_light_does_not_enable_fast_mode(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light", "roles": {"bug-fix": [{
                "providerInstanceId": "claudeAgent",
                "model": "claude-opus-5-5",
                "options": {"effort": "xhigh", "fastMode": False},
            }]}})
            held = repo.show_bug_fix()
            repo.put(repo.user, {"mode": "light", "roles": {"bug-fix": [{
                "providerInstanceId": "claudeAgent",
                "model": "claude-opus-5-5",
                "options": {"effort": "xhigh"},
            }]}})
            absent = repo.show_bug_fix()
        self.assertEqual(held.returncode, 0, held.stderr)
        self.assertEqual(absent.returncode, 0, absent.stderr)
        self.assertEqual(json.loads(held.stdout)["roles"]["bug-fix"]["seats"], [{
            "providerInstanceId": "claudeAgent",
            "model": "claude-opus-5-5",
            "options": {"effort": "medium", "fastMode": False},
        }])
        self.assertEqual(json.loads(absent.stdout)["roles"]["bug-fix"]["seats"], [{
            "providerInstanceId": "claudeAgent",
            "model": "claude-opus-5-5",
            "options": {"effort": "medium"},
        }])
