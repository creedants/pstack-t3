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
import tempfile
import unittest
from pathlib import Path
from tests import in_process
from .support import CATALOG, NO_PARENT_INFO, ROOT, Repo, setUpModule


class RelativeConfigHomeCliTest(unittest.TestCase):
    OPUS = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
    GROK = {"providerInstanceId": "grok", "model": "grok-4.7"}

    def setUp(self):
        wrapper = tempfile.TemporaryDirectory()
        self.addCleanup(wrapper.cleanup)
        root = Path(os.path.realpath(wrapper.name))
        self.home, self.work = root / "home", root / "work"
        self.home.mkdir()
        (self.work / ".git").mkdir(parents=True)
        self.default = self.home / ".config" / "pstack-t3"
        self.earlier = self.work / ".config" / "pstack-t3"
        self.ignored = (
            "XDG_CONFIG_HOME='.config' is not an absolute path, so it is ignored "
            f"and the config home is {str(self.home / '.config')!r}\n"
        )

    def put(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def run_roles(self, *args):
        env = {**os.environ, "HOME": str(self.home), "XDG_CONFIG_HOME": ".config"}
        invocation = in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=tuple(args),
            env=env,
            cwd=self.work,
        )
        if args == ("show", "--help"):
            return in_process.finished(in_process.Reason.HELP, invocation)
        return in_process.invoke(invocation)

    def test_show_under_a_relative_xdg_config_home_reads_the_default_roles_file_and_prints_the_ignored_line_once(self):
        self.put(self.default / "roles.json", {"version": 1, "roles": {"bug-fix": [self.OPUS]}})
        self.put(self.default / "catalog.json", json.loads(CATALOG.read_text()))
        self.put(self.earlier / "roles.json", {"version": 1, "roles": {"bug-fix": [self.GROK]}})
        completed = self.run_roles("show", "--cwd", str(self.work), "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, self.ignored + NO_PARENT_INFO)
        self.assertEqual(json.loads(completed.stdout), {
            "budget": "default",
            "mode": "light",
            "modeSource": "default",
            "escalate": None,
            "catalog": True,
            "roles": {"bug-fix": {"source": str(self.default / "roles.json"), "seats": [
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            ]}},
        })

    def test_write_under_a_relative_xdg_config_home_writes_under_the_default_config_home_and_leaves_the_working_directory_unchanged(self):
        self.put(self.earlier / "roles.json", {"version": 1, "roles": {"bug-fix": [self.OPUS]}})
        self.put(self.earlier / "catalog.json", {"providers": []})
        kept = {name: (self.earlier / name).read_bytes() for name in ("catalog.json", "roles.json")}
        completed = self.run_roles("write", "--cwd", str(self.work), "--catalog", str(CATALOG), "--set", "bug-fix=grok/grok-4.7")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, f"wrote {self.default / 'roles.json'}\n")
        self.assertEqual(completed.stderr, self.ignored)
        self.assertEqual(
            json.loads((self.default / "roles.json").read_text()),
            {"version": 1, "roles": {"bug-fix": [self.GROK]}, "budget": "default", "mode": "light"},
        )
        self.assertEqual(json.loads((self.default / "catalog.json").read_text()), json.loads(CATALOG.read_text()))
        self.assertEqual(sorted(os.listdir(self.work)), [".config", ".git"])
        self.assertEqual(os.listdir(self.work / ".config"), ["pstack-t3"])
        self.assertEqual({name: (self.earlier / name).read_bytes() for name in sorted(os.listdir(self.earlier))}, kept)

    def test_a_roles_command_that_reads_no_user_config_home_prints_no_ignored_line(self):
        brief = self.work / "brief.md"
        brief.write_text("x\n", encoding="utf-8")
        roles_file = self.work / "roles.json"
        self.put(roles_file, {"version": 1, "roles": {}, "mode": "light"})
        runs = (
            (("check-brief", str(brief)), 1),
            (("show", "--help"), 0),
            (("mode", "--cwd", str(self.work), "--config", str(roles_file)), 0),
        )
        for args, status in runs:
            with self.subTest(args=args):
                completed = self.run_roles(*args)
                self.assertEqual(completed.returncode, status, completed.stdout + completed.stderr)
                self.assertEqual(completed.stderr, "")

    def test_a_refused_show_under_a_relative_xdg_config_home_prints_the_ignored_line_then_the_error_and_exits_2(self):
        file = self.default / "roles.json"
        file.parent.mkdir(parents=True)
        file.write_text("{", encoding="utf-8")
        completed = self.run_roles("show", "--cwd", str(self.work), "--role", "bug-fix")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, (
            self.ignored
            + f"error: {file}: invalid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)\n"
        ))


class WriteFilesCliTest(unittest.TestCase):
    def setUp(self):
        wrapper = tempfile.TemporaryDirectory()
        self.addCleanup(wrapper.cleanup)
        self.home = Path(os.path.realpath(wrapper.name))
        (self.home / ".git").mkdir()

    def write(self, config_home):
        env = {**os.environ, "HOME": str(self.home), "XDG_CONFIG_HOME": config_home}
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=("write", "--cwd", str(self.home), "--catalog", str(CATALOG), "--set", "bug-fix=grok/grok-4.7"),
            env=env,
            cwd=self.home,
        ))

    def temporary_files(self):
        return sorted(str(path.relative_to(self.home)) for path in self.home.rglob("tmp*"))

    def test_write_under_a_config_home_whose_dot_dot_follows_a_symlink_writes_both_files_in_the_directory_the_system_reaches(self):
        (self.home / "else" / "sub").mkdir(parents=True)
        os.symlink(self.home / "else" / "sub", self.home / "x")
        given = f"{self.home}/x/../.config"
        completed = self.write(given)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, f"wrote {given}/pstack-t3/roles.json\n")
        self.assertEqual(completed.stderr, "")
        reached = self.home / "else" / ".config" / "pstack-t3"
        self.assertEqual(sorted(os.listdir(reached)), ["catalog.json", "roles.json"])
        self.assertEqual(
            json.loads((reached / "roles.json").read_text()),
            {"version": 1, "roles": {"bug-fix": [{"providerInstanceId": "grok", "model": "grok-4.7"}]}, "budget": "default", "mode": "light"},
        )
        self.assertEqual(json.loads((reached / "catalog.json").read_text()), json.loads(CATALOG.read_text()))
        self.assertEqual(self.temporary_files(), [])
        self.assertFalse(os.path.lexists(self.home / ".config"))

    def test_a_write_whose_catalog_json_is_a_directory_leaves_no_temporary_file(self):
        state = self.home / ".config" / "pstack-t3"
        (state / "catalog.json").mkdir(parents=True)
        completed = self.write(str(self.home / ".config"))
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(os.listdir(state / "catalog.json"), [])
        self.assertEqual(self.temporary_files(), [])


class WritePreservationTest(unittest.TestCase):
    def targets(self, repo):
        return (
            (repo.user, ()),
            (repo.directory / "custom.json", ("--config", str(repo.directory / "custom.json"))),
            (repo.project, ("--project",)),
        )

    def test_overlay_keeps_unnamed_settings_in_any_project(self):
        for target_index in range(3):
            for budget_flags in ((), ("--budget", "small")):
                for keep_flags in ((), ("--keep",)):
                    with self.subTest(target=target_index, budget=budget_flags, keep=keep_flags):
                        with tempfile.TemporaryDirectory() as directory:
                            repo = Repo(directory)
                            target, target_flags = self.targets(repo)[target_index]
                            seed = {
                                "version": 1,
                                "roles": {
                                    "bug-fix": ["inherit"],
                                    "hillclimb": ["inherit"],
                                    "arena runners": ["inherit", "inherit"],
                                },
                                "budget": "large",
                                "mode": "light",
                                "escalate": ["**/migrations/**"],
                                "opencodeAllow": [],
                                "custom": {"nested": [None, False, "seat"]},
                            }
                            repo.put(target, seed)
                            completed = repo.write(
                                *target_flags, *budget_flags, *keep_flags,
                                "--set", "bug-fix=grok/grok-4.7",
                                "--set", "arena runners=claudeAgent/claude-opus-5-5",
                            )
                            self.assertEqual(completed.returncode, 0, completed.stderr)
                            expected = {**seed, "roles": {**seed["roles"],
                                "bug-fix": [{"providerInstanceId": "grok", "model": "grok-4.7"}],
                                "arena runners": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
                            }}
                            if budget_flags:
                                expected["budget"] = "small"
                            self.assertEqual(json.loads(target.read_text()), expected)
                            self.assertFalse(target.with_name(target.name + ".bak").exists())
                            snapshot = repo.user.with_name("catalog.json")
                            self.assertEqual(snapshot.exists(), target_index == 0)
                            if snapshot.exists():
                                self.assertEqual(json.loads(snapshot.read_text()), json.loads(CATALOG.read_text()))

    def test_null_settings_are_refused_until_a_flag_names_them(self):
        for target_index in range(3):
            for key, valid, exit_code in (("budget", "large", 2), ("mode", "light", 1)):
                with self.subTest(target=target_index, key=key):
                    with tempfile.TemporaryDirectory() as directory:
                        repo = Repo(directory)
                        target, target_flags = self.targets(repo)[target_index]
                        repo.put(target, {"roles": {}, key: None})
                        before = target.read_bytes()
                        completed = repo.write(*target_flags)
                        self.assertEqual(completed.returncode, exit_code, completed.stderr)
                        self.assertIn(str(target), completed.stderr)
                        self.assertEqual(target.read_bytes(), before)
                        self.assertFalse(target.with_name(target.name + ".bak").exists())
                        repaired = repo.write(*target_flags, "--" + key, valid)
                        self.assertEqual(repaired.returncode, 0, repaired.stderr)
                        self.assertEqual(json.loads(target.read_text())[key], valid)

    def test_malformed_roles_are_refused_even_with_replace(self):
        for target_index in range(3):
            for value in ([], None, "seat", 1, True):
                for flags in ((), ("--keep",), ("--replace",), ("--replace", "--force")):
                    with self.subTest(target=target_index, value=value, flags=flags):
                        with tempfile.TemporaryDirectory() as directory:
                            repo = Repo(directory)
                            target, target_flags = self.targets(repo)[target_index]
                            repo.put(target, {"roles": value, "custom": "keep"})
                            before = target.read_bytes()
                            completed = repo.write(*target_flags, *flags)
                            self.assertEqual(completed.returncode, 2, completed.stderr)
                            self.assertIn(f"{target}: roles must be an object", completed.stderr)
                            self.assertEqual(target.read_bytes(), before)
                            self.assertFalse(target.with_name(target.name + ".bak").exists())
                            self.assertFalse(repo.user.with_name("catalog.json").exists())

    def test_unreadable_json_is_refused_for_every_target_and_replace(self):
        for target_index in range(3):
            for content in (b"{not json", b"\xff\xfe"):
                for flags in ((), ("--replace",)):
                    with self.subTest(target=target_index, content=content, flags=flags):
                        with tempfile.TemporaryDirectory() as directory:
                            repo = Repo(directory)
                            target, target_flags = self.targets(repo)[target_index]
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(content)
                            completed = repo.write(*target_flags, *flags)
                            self.assertEqual(completed.returncode, 2, completed.stderr)
                            self.assertIn(f"{target}: invalid JSON:", completed.stderr)
                            self.assertEqual(target.read_bytes(), content)
                            self.assertFalse(target.with_name(target.name + ".bak").exists())

    def test_backup_only_for_replace_and_only_for_existing_targets(self):
        for target_index in range(3):
            for flags in ((), ("--keep",), ("--replace",)):
                with self.subTest(target=target_index, flags=flags):
                    with tempfile.TemporaryDirectory() as directory:
                        repo = Repo(directory)
                        target, target_flags = self.targets(repo)[target_index]
                        first = repo.write(*target_flags, *flags)
                        self.assertEqual(first.returncode, 0, first.stderr)
                        backup = target.with_name(target.name + ".bak")
                        self.assertFalse(backup.exists())
                        before = target.read_bytes()
                        second = repo.write(*target_flags, *flags, "--budget", "small")
                        self.assertEqual(second.returncode, 0, second.stderr)
                        self.assertEqual(backup.exists(), "--replace" in flags)
                        if backup.exists():
                            self.assertEqual(backup.read_bytes(), before)
