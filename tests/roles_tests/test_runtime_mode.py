import json
import tempfile
import unittest
from .support import CATALOG, Repo, setUpModule
from .catalogs import BUNNY_SEAT, OPUS_BACKUP, PANEL_RULE, STEP_SEAT, VERIFIER_OUT, backup_catalog, run_backup


MUSE_DRIVER_SEAT = {"providerInstanceId": "muse", "model": "muse-spark-1"}


def muse_driver_provider(provider_id="muse", driver="muse"):
    return {
        "providerInstanceId": provider_id,
        "driverKind": driver,
        "canRunChildTask": True,
        "constraints": [],
        "models": [{"id": "muse-spark-1", "options": [], "cost": 0}],
    }


def lacks_mode_note(mode, provider_id="muse"):
    return (
        f"{provider_id} is not runnable (the muse driver lacks runtime mode {mode}); "
        "seat inherits the parent"
    )


class RuntimeModeCliTest(unittest.TestCase):
    def show(self, role, roles_file=None, mode=None, provider=None, catalog_mode=None,
             parent="claudeAgent/claude-opus-5-5"):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog = json.loads(CATALOG.read_text())
            catalog["providers"].append(provider or muse_driver_provider())
            if catalog_mode is not None:
                catalog["runtimeMode"] = catalog_mode
            path = repo.directory / "catalog.json"
            repo.put(path, catalog)
            if roles_file is not None:
                repo.put(repo.user, roles_file)
            flag = () if mode is None else ("--runtime-mode", mode)
            completed = repo.run(
                "show", "--catalog", str(path), "--parent", parent, "--role", role, *flag,
            )
            return completed, str(repo.user)

    def entry(self, role, **kwargs):
        completed, user = self.show(role, **kwargs)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)["roles"][role], user

    BUG_FIX = {"roles": {"bug-fix": [MUSE_DRIVER_SEAT]}}

    def test_auto_replaces_a_configured_seat_on_a_muse_driver_with_inherit(self):
        entry, user = self.entry("bug-fix", roles_file=self.BUG_FIX, mode="auto")
        self.assertEqual(entry, {
            "source": user,
            "seats": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}],
            "info": ["inherit made explicit as claudeAgent/claude-opus-5-5 so the small budget applies"],
            "notes": ["muse is not runnable (the muse driver lacks runtime mode auto); seat inherits the parent"],
        })

    def test_auto_accept_edits_and_an_unlisted_mode_replace_the_seat_and_name_the_mode(self):
        for mode in ("auto-accept-edits", "yolo"):
            with self.subTest(mode=mode):
                entry, user = self.entry("bug-fix", roles_file=self.BUG_FIX, mode=mode)
                self.assertEqual(entry, {
                    "source": user,
                    "seats": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}],
                    "info": ["inherit made explicit as claudeAgent/claude-opus-5-5 so the small budget applies"],
                    "notes": [lacks_mode_note(mode)],
                })

    def test_supported_modes_and_a_catalog_file_mode_keep_the_seat_without_a_note(self):
        cases = (
            {"mode": "full-access"},
            {"mode": "approval-required"},
            {"catalog_mode": "auto"},
        )
        for case in cases:
            with self.subTest(**case):
                entry, user = self.entry("bug-fix", roles_file=self.BUG_FIX, **case)
                self.assertEqual(entry, {"source": user, "seats": [MUSE_DRIVER_SEAT]})

    def test_the_match_is_on_driver_kind_not_on_the_provider_id(self):
        work_seat = {"providerInstanceId": "work", "model": "muse-spark-1"}
        replaced, user = self.entry(
            "bug-fix",
            roles_file={"roles": {"bug-fix": [work_seat]}},
            mode="auto",
            provider=muse_driver_provider("work", "muse"),
        )
        kept, _user = self.entry(
            "bug-fix",
            roles_file=self.BUG_FIX,
            mode="auto",
            provider=muse_driver_provider("muse", "opencode"),
        )
        self.assertEqual(replaced, {
            "source": user,
            "seats": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}],
            "info": ["inherit made explicit as claudeAgent/claude-opus-5-5 so the small budget applies"],
            "notes": ["work is not runnable (the muse driver lacks runtime mode auto); seat inherits the parent"],
        })
        self.assertEqual(kept["seats"], [MUSE_DRIVER_SEAT])
        self.assertNotIn("notes", kept)

    def test_auto_replaces_the_muse_seat_of_a_panel_in_place(self):
        opus = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
        entry, _user = self.entry(
            "arena runners",
            roles_file={"roles": {"arena runners": [MUSE_DRIVER_SEAT, opus]}, "mode": "full"},
            mode="auto",
        )
        self.assertEqual(entry["seats"], ["inherit", opus])

    def test_auto_drops_a_review_backups_seat_on_a_muse_driver(self):
        opus = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
        grok = {"providerInstanceId": "grok", "model": "grok-4.7"}
        entry, user = self.entry(
            "review backups",
            roles_file={"roles": {"review backups": [MUSE_DRIVER_SEAT, opus, grok]}},
            mode="auto",
        )
        self.assertEqual(entry, {
            "source": user,
            "seats": [opus, grok],
            "notes": ["dropped muse/muse-spark-1: not runnable or not in the catalog"],
        })

    def test_an_empty_mode_and_a_mode_with_whitespace_exit_2(self):
        for text in ("", "a b"):
            with self.subTest(text=text):
                completed, _user = self.show("bug-fix", roles_file=self.BUG_FIX, mode=text)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(
                    completed.stderr,
                    f"error: --runtime-mode {text!r}: expected runtimeMode from orchestrator_capabilities\n",
                )

    def test_validate_and_write_do_not_take_the_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            for command in ("validate", "write"):
                with self.subTest(command=command):
                    completed = repo.run(command, "--catalog", str(CATALOG), "--runtime-mode", "auto")
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(completed.stdout, "")
                    self.assertIn("--runtime-mode", completed.stderr)
                    self.assertFalse(repo.user.exists())

    MUSE_PARENT = "muse/muse-spark-1"
    WRITTEN_INHERIT = {"roles": {"judgment and prose": ["inherit"]}}

    def assert_refused(self, role, roles_file):
        completed, _user = self.show(role, roles_file=roles_file, mode="auto", parent=self.MUSE_PARENT)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: role {role!r} cannot inherit muse/muse-spark-1: "
            "muse is not runnable (the muse driver lacks runtime mode auto)\n",
        )

    def test_auto_refuses_a_written_inherit_on_a_muse_driver_parent(self):
        self.assert_refused("judgment and prose", self.WRITTEN_INHERIT)

    def test_auto_refuses_a_configured_muse_driver_seat_on_a_muse_driver_parent(self):
        self.assert_refused("bug-fix", self.BUG_FIX)

    def test_auto_refuses_a_panel_that_holds_inherit_on_a_muse_driver_parent(self):
        opus = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
        self.assert_refused("arena runners", {"roles": {"arena runners": ["inherit", opus]}})

    def test_supported_modes_and_a_catalog_file_mode_keep_a_written_inherit_on_a_muse_driver_parent(self):
        cases = (
            {"mode": "full-access"},
            {"mode": "approval-required"},
            {"catalog_mode": "auto"},
        )
        for case in cases:
            with self.subTest(**case):
                entry, user = self.entry(
                    "judgment and prose", roles_file=self.WRITTEN_INHERIT, parent=self.MUSE_PARENT, **case,
                )
                self.assertEqual(entry, {"source": user, "seats": ["inherit"]})

    def test_auto_keeps_a_written_inherit_on_a_claude_parent(self):
        entry, user = self.entry(
            "judgment and prose",
            roles_file={**self.WRITTEN_INHERIT, "mode": "full"},
            mode="auto",
        )
        self.assertEqual(entry, {"source": user, "seats": ["inherit"]})

    def test_validate_accepts_a_written_inherit_on_a_muse_driver_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog = json.loads(CATALOG.read_text())
            catalog["providers"].append(muse_driver_provider())
            path = repo.directory / "catalog.json"
            repo.put(path, catalog)
            repo.put(repo.user, self.WRITTEN_INHERIT)
            completed = repo.run("validate", "--catalog", str(path), "--parent", self.MUSE_PARENT)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "ok\n")


class RuntimeModeBackupCliTest(unittest.TestCase):
    PANEL = {"roles": {"review backups": [MUSE_DRIVER_SEAT, STEP_SEAT, BUNNY_SEAT]}}

    def backup(self, *args, roles_file=None):
        catalog = backup_catalog()
        catalog["providers"].append(muse_driver_provider())
        return run_backup(*args, catalog=catalog, roles_file=roles_file)

    def payload(self, *args, roles_file=None):
        completed = self.backup(*args, roles_file=roles_file)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_auto_drops_a_muse_driver_seat_from_a_three_seat_panel(self):
        payload = self.payload(*VERIFIER_OUT, "--runtime-mode", "auto", roles_file=self.PANEL)
        self.assertEqual(payload, {
            "decision": "panel",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seats": [STEP_SEAT, BUNNY_SEAT],
            "rule": PANEL_RULE,
            "notes": ["dropped muse/muse-spark-1: not runnable or not in the catalog"],
            "report": (
                "verifiers: codex/gpt-6.1-sol hit its usage limit; every paid reviewer backup is out, "
                "so review backups runs 2 seats; land only if no reviewer reproduces a blocker and at least two pass"
            ),
        })

    def test_auto_parks_a_two_seat_panel_with_a_muse_driver_seat(self):
        payload = self.payload(
            *VERIFIER_OUT, "--runtime-mode", "auto",
            roles_file={"roles": {"review backups": [MUSE_DRIVER_SEAT, STEP_SEAT]}},
        )
        self.assertEqual(payload, {
            "decision": "park",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "report": (
                "verifiers: codex/gpt-6.1-sol hit its usage limit; review backups has 1 usable seat and needs 2, "
                "so the work waits for the reset (dropped muse/muse-spark-1: not runnable or not in the catalog)"
            ),
        })

    def test_auto_resumes_a_bug_fix_seat_on_a_muse_driver_on_opus(self):
        payload = self.payload(
            "--role", "bug-fix", "--provider", "muse", "--model", "muse-spark-1", "--resume",
            "--runtime-mode", "auto",
            roles_file={"backups": {"bug-fix": OPUS_BACKUP}},
        )
        self.assertEqual(payload, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "muse/muse-spark-1",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: muse/muse-spark-1 resumed on claudeAgent/claude-opus-5-5 at medium after the reset",
        })

    def test_an_empty_mode_and_a_mode_with_whitespace_exit_2(self):
        for text in ("", "a b"):
            with self.subTest(text=text):
                completed = self.backup(*VERIFIER_OUT, "--runtime-mode", text, roles_file=self.PANEL)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(
                    completed.stderr,
                    f"error: --runtime-mode {text!r}: expected runtimeMode from orchestrator_capabilities\n",
                )

    def test_full_access_and_no_flag_keep_the_muse_driver_seat_first_in_a_three_seat_panel(self):
        for flag in (("--runtime-mode", "full-access"), ()):
            with self.subTest(flag=flag):
                payload = self.payload(*VERIFIER_OUT, *flag, roles_file=self.PANEL)
                self.assertEqual(payload, {
                    "decision": "panel",
                    "role": "verifiers",
                    "failed": "codex/gpt-6.1-sol",
                    "seats": [MUSE_DRIVER_SEAT, STEP_SEAT, BUNNY_SEAT],
                    "rule": PANEL_RULE,
                    "report": (
                        "verifiers: codex/gpt-6.1-sol hit its usage limit; every paid reviewer backup is out, "
                        "so review backups runs 3 seats; land only if no reviewer reproduces a blocker and at least two pass"
                    ),
                })
