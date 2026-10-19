import json
import os
import tempfile
import unittest
from tests import in_process
from .support import CATALOG, ROOT, Repo, roles, setUpModule
from .catalogs import LIMIT, MUSE, MUSE_SEAT, STEP, STEP_SEAT, VERIFIER_OUT, backup_catalog, metered_line, plain_provider


UNSET_NOTE = roles.UNSET_NOTE


class ReviewBackupsRoleCliTest(unittest.TestCase):
    def test_show_reports_unset_with_and_without_a_catalog(self):
        expected = {"source": "default", "seats": "unset", "note": UNSET_NOTE}
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            parent = ("--parent", "claudeAgent/claude-opus-5-5", "--role", "review backups")
            plain = repo.run("show", *parent)
            with_catalog = repo.run("show", "--catalog", str(CATALOG), *parent)
        for completed in (plain, with_catalog):
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["roles"], {"review backups": expected})

    def test_write_and_validate_refuse_inherit(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            written = repo.write("--set", "review backups=inherit;grok/grok-4.7")
            repo.put(repo.user, {"roles": {"review backups": ["inherit"]}})
            validated = repo.run("validate", "--catalog", str(CATALOG))
            user = str(repo.user)
        refusal = "role 'review backups' refuses inherit, because the parent can be the author\n"
        self.assertEqual(written.returncode, 2)
        self.assertEqual(written.stderr, f"error: {user}: {refusal}")
        self.assertEqual(validated.returncode, 2)
        self.assertEqual(validated.stderr, f"error: {user}: {refusal}")

    PANEL_CONFIG = {"roles": {"review backups": [
        MUSE_SEAT,
        {"providerInstanceId": "kilo", "model": "kilo-1"},
        {"providerInstanceId": "opencode", "model": "opencode/nope-free"},
        STEP_SEAT,
        {"providerInstanceId": "openrouter", "model": "muse-lite-2"},
    ]}}
    PANEL_SET = (
        "review backups=opencode/opencode/muse-lite-2-free?variant=high;kilo/kilo-1;"
        "opencode/opencode/nope-free;opencode/opencode/step-9-preview-free?variant=high;"
        "openrouter/muse-lite-2"
    )
    PANEL_NOTES = [
        "dropped kilo/kilo-1: not runnable or not in the catalog",
        "dropped opencode/opencode/nope-free: not runnable or not in the catalog",
        "dropped openrouter/muse-lite-2: family muse already seated",
    ]
    PANEL_PROBLEMS = "".join(f"review backups: {note}\n" for note in PANEL_NOTES)

    def panel_repo(self, directory, roles_file=None):
        repo = Repo(directory)
        catalog = backup_catalog()
        catalog["providers"].append(plain_provider("kilo", "kilo-1", runs=False))
        catalog["providers"].append(plain_provider("openrouter", "muse-lite-2"))
        path = repo.directory / "catalog.json"
        repo.put(path, catalog)
        if roles_file is not None:
            repo.put(repo.user, roles_file)
        return repo, str(path)

    def test_show_and_backup_print_the_panel_literals_for_one_config_and_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, self.PANEL_CONFIG)
            shown = repo.run("show", "--catalog", catalog, "--role", "review backups")
            backed = in_process.invoke(in_process.Invocation(
                program=ROOT / "t3/scripts/roles.py",
                argv=("backup", "--cwd", str(repo.directory), "--catalog", catalog, *VERIFIER_OUT),
                env={**os.environ, "XDG_CONFIG_HOME": str(repo.directory)},
                stdin=LIMIT,
            ))
            user = str(repo.user)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["roles"], {"review backups": {
            "source": user,
            "seats": [MUSE_SEAT, STEP_SEAT],
            "notes": self.PANEL_NOTES + [metered_line("review backups", "opencode", "opencode/nope-free")],
        }})
        self.assertEqual(backed.returncode, 0, backed.stderr)
        payload = json.loads(backed.stdout)
        self.assertEqual(payload["decision"], "panel")
        self.assertEqual(payload["seats"], [MUSE_SEAT, STEP_SEAT])
        self.assertEqual(payload["notes"], self.PANEL_NOTES)

    def test_validate_prints_a_problem_for_each_dropped_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, self.PANEL_CONFIG)
            completed = repo.run("validate", "--catalog", catalog)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            completed.stdout,
            metered_line("review backups", "opencode", "opencode/nope-free") + "\n" + self.PANEL_PROBLEMS,
        )
        self.assertEqual(completed.stderr, "")

    def test_write_refuses_dropped_seats_without_force(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory)
            refused = repo.run("write", "--catalog", catalog, "--set", self.PANEL_SET)
            written_before_force = repo.user.exists()
            forced = repo.run("write", "--catalog", catalog, "--set", self.PANEL_SET, "--force")
            stored = json.loads(repo.user.read_text())
            user = str(repo.user)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(refused.stdout, "")
        self.assertEqual(
            refused.stderr,
            "error: refusing to write without --force:\n"
            + self.PANEL_PROBLEMS
            + metered_line("review backups", "opencode", "opencode/nope-free")
            + "\n",
        )
        self.assertFalse(written_before_force)
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertEqual(forced.stdout, f"wrote {user}\n")
        self.assertEqual(stored["roles"], self.PANEL_CONFIG["roles"])

    def test_show_without_a_catalog_reports_catalog_required(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, _catalog = self.panel_repo(directory, self.PANEL_CONFIG)
            completed = repo.run("show", "--role", "review backups")
            user = str(repo.user)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"], {"review backups": {
            "source": user,
            "seats": "catalog-required",
            "note": (
                "review backups drops seats by the catalog: "
                "call orchestrator_capabilities and rerun roles.py show --catalog"
            ),
            "notes": [
                metered_line("review backups", "opencode", MUSE),
                metered_line("review backups", "opencode", "opencode/nope-free"),
                metered_line("review backups", "opencode", STEP),
            ],
        }})

    def test_validate_reports_an_unknown_option_on_a_kept_seat(self):
        unknown = {**MUSE_SEAT, "options": {"variant": "high", "bogus": "1"}}
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, {"roles": {"review backups": [unknown, STEP_SEAT]}})
            completed = repo.run("validate", "--catalog", catalog)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            completed.stdout,
            "review backups: opencode/opencode/muse-lite-2-free: dropped unknown options bogus\n",
        )

    ONE_SEAT = {"roles": {"review backups": [MUSE_SEAT]}}
    SHORTFALL = "review backups: has 1 usable seat and needs 2\n"

    def test_validate_reports_one_usable_seat_and_passes_two(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, self.ONE_SEAT)
            one = repo.run("validate", "--catalog", catalog)
            repo.put(repo.user, {"roles": {"review backups": [MUSE_SEAT, STEP_SEAT]}})
            two = repo.run("validate", "--catalog", catalog)
        self.assertEqual(one.returncode, 1)
        self.assertEqual(one.stdout, self.SHORTFALL)
        self.assertEqual(one.stderr, "")
        self.assertEqual(two.returncode, 0, two.stderr)
        self.assertEqual(two.stdout, "ok\n")

    def test_validate_and_backup_count_one_usable_seat_for_one_config_and_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, self.ONE_SEAT)
            validated = repo.run("validate", "--catalog", catalog)
            backed = in_process.invoke(in_process.Invocation(
                program=ROOT / "t3/scripts/roles.py",
                argv=("backup", "--cwd", str(repo.directory), "--catalog", catalog, *VERIFIER_OUT),
                env={**os.environ, "XDG_CONFIG_HOME": str(repo.directory)},
                stdin=LIMIT,
            ))
        self.assertEqual(validated.returncode, 1)
        self.assertEqual(validated.stdout, self.SHORTFALL)
        self.assertEqual(backed.returncode, 0, backed.stderr)
        payload = json.loads(backed.stdout)
        self.assertEqual(payload["decision"], "park")
        self.assertEqual(
            payload["report"],
            "verifiers: codex/gpt-6.1-sol hit its usage limit; review backups has 1 usable seat and needs 2, "
            "so the work waits for the reset",
        )

    def test_write_refuses_one_usable_seat_without_force(self):
        one_seat = "review backups=opencode/opencode/muse-lite-2-free?variant=high"
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory)
            refused = repo.run("write", "--catalog", catalog, "--set", one_seat)
            written_before_force = repo.user.exists()
            forced = repo.run("write", "--catalog", catalog, "--set", one_seat, "--force")
            stored = json.loads(repo.user.read_text())
            user = str(repo.user)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(refused.stdout, "")
        self.assertEqual(
            refused.stderr,
            "error: refusing to write without --force:\n" + self.SHORTFALL,
        )
        self.assertFalse(written_before_force)
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertEqual(forced.stdout, f"wrote {user}\n")
        self.assertEqual(stored["roles"], self.ONE_SEAT["roles"])

    def test_show_notes_one_usable_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, self.ONE_SEAT)
            completed = repo.run("show", "--catalog", catalog, "--role", "review backups")
            user = str(repo.user)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"], {"review backups": {
            "source": user,
            "seats": [MUSE_SEAT],
            "notes": ["has 1 usable seat and needs 2"],
        }})

    def test_show_reports_a_role_set_to_one_excluded_seat_as_set(self):
        excluded = {"providerInstanceId": "grok", "model": "grok-4.7"}
        skipped = [
            "skipped configured seat grok/grok-4.7: grok-4.7 matches exclude entry 1 (grok-4.7), "
            f"and {roles.EXCLUDE_RULE}",
        ]
        excluded_file = {"roles": {"review backups": [excluded]}, "exclude": [{"model": "grok-4.7"}]}
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, excluded_file)
            with_catalog = repo.run("show", "--catalog", catalog, "--role", "review backups")
            plain = repo.run("show", "--role", "review backups")
            user = str(repo.user)
        self.assertEqual(with_catalog.returncode, 0, with_catalog.stderr)
        self.assertEqual(json.loads(with_catalog.stdout)["roles"], {"review backups": {
            "source": user,
            "seats": [],
            "notes": skipped + ["has 0 usable seats and needs 2"],
        }})
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(json.loads(plain.stdout)["roles"], {"review backups": {
            "source": user,
            "seats": "catalog-required",
            "note": (
                "review backups drops seats by the catalog: "
                "call orchestrator_capabilities and rerun roles.py show --catalog"
            ),
            "notes": skipped,
        }})

    def test_validate_reports_a_role_set_to_one_excluded_seat_as_excluded_and_short(self):
        excluded = {"providerInstanceId": "grok", "model": "grok-4.7"}
        excluded_line = (
            "review backups: grok/grok-4.7: grok-4.7 matches exclude entry 1 (grok-4.7), "
            f"and {roles.EXCLUDE_RULE}"
        )
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(
                directory, {"roles": {"review backups": [excluded]}, "exclude": [{"model": "grok-4.7"}]}
            )
            validated = repo.run("validate", "--catalog", catalog)
        self.assertEqual(validated.returncode, 1)
        self.assertEqual(validated.stdout, f"{excluded_line}\nreview backups: has 0 usable seats and needs 2\n")
        self.assertEqual(validated.stderr, "")
