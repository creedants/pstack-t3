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
from tests import in_process
from .support import FOUR_PATHS, INHERIT_ENTRY, ModeCliCase, ROOT, ROOT_ALIASES, Repo, roles, setUpModule


def bug_fix_show(mode, mode_source, escalate, budget="default"):
    return {
        "budget": budget,
        "mode": mode,
        "modeSource": mode_source,
        "escalate": escalate,
        "catalog": True,
        "roles": {"bug-fix": INHERIT_ENTRY},
    }


class ModeCliTest(ModeCliCase):
    def test_project_mode_wins(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            repo.put(repo.project, {"mode": "full", "escalate": FOUR_PATHS})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload), ["budget", "mode", "modeSource", "escalate", "catalog", "roles"])
        self.assertEqual(payload, bug_fix_show("full", str(repo.project), FOUR_PATHS))


    def test_missing_mode_defaults_to_light(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload, bug_fix_show("light", "default", None))
        self.assertEqual(payload["roles"]["bug-fix"], INHERIT_ENTRY)


    def test_project_without_mode_uses_the_user_mode(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            bug_fix_show("light", str(repo.user), None),
        )


    def test_user_escalate_list_is_ignored(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light", "escalate": ["**/migrations/**"]})
            repo.put(repo.project, {})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["escalate"], None)
        self.assertEqual(payload["mode"], "light")
        self.assertEqual(payload["modeSource"], str(repo.user))


    def test_unknown_user_mode_exits_1(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "turbo"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.user}: mode 'turbo' is not one of full, light\n",
        )


    def test_unknown_project_mode_exits_1(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "turbo"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.project}: mode 'turbo' is not one of full, light\n",
        )


    def test_escalate_string_exits_1(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"escalate": "**/migrations/**"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.project}: escalate must be a list of strings\n",
        )


    def test_escalate_empty_string_exits_1(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"escalate": ["ok", ""]})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.project}: escalate must be a list of strings\n",
        )


    def test_show_preserves_root_normalized_escalate_patterns(self):
        for pattern in ROOT_ALIASES:
            with self.subTest(pattern=pattern):
                with self.open_repo() as directory:
                    repo = Repo(directory)
                    repo.put(repo.project, {"mode": "full", "escalate": [pattern]})
                    completed = repo.show_bug_fix()
                expected = {
                    "budget": "default",
                    "mode": "full",
                    "modeSource": str(repo.project),
                    "escalate": [pattern],
                    "catalog": True,
                    "roles": {
                        "bug-fix": {
                            "source": "default",
                            "seats": "inherit",
                            "note": roles.UNSET_ROLE_NOTE,
                        },
                    },
                }
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, json.dumps(expected, indent=2) + "\n")
                self.assertEqual(completed.stderr, "")


    def test_project_keep_preserves_root_normalized_escalate_patterns(self):
        for pattern in ROOT_ALIASES:
            with self.subTest(pattern=pattern):
                with self.open_repo() as directory:
                    repo = Repo(directory)
                    repo.put(
                        repo.project,
                        {"version": 1, "roles": {}, "mode": "light", "escalate": [pattern]},
                    )
                    first = repo.write("--project", "--keep")
                    self.assertEqual(first.returncode, 0)
                    self.assertEqual(first.stdout, f"wrote {repo.project}\n")
                    self.assertEqual(first.stderr, "")
                    self.assertEqual(
                        json.loads(repo.project.read_text()),
                        {"version": 1, "roles": {}, "mode": "light", "escalate": [pattern]},
                    )
                    written = repo.project.read_bytes()
                    second = repo.write("--project", "--keep")
                    self.assertEqual(second.returncode, 0)
                    self.assertEqual(second.stdout, f"wrote {repo.project}\n")
                    self.assertEqual(second.stderr, "")
                    self.assertEqual(repo.project.read_bytes(), written)


    def test_project_rewrite_preserves_root_normalized_escalate_patterns(self):
        for pattern in ROOT_ALIASES:
            with self.subTest(pattern=pattern):
                with self.open_repo() as directory:
                    repo = Repo(directory)
                    repo.put(
                        repo.project,
                        {"version": 1, "roles": {}, "mode": "light", "escalate": [pattern]},
                    )
                    completed = repo.write("--project", "--replace")
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual(completed.stdout, f"wrote {repo.project}\n")
                    self.assertEqual(completed.stderr, "")
                    self.assertEqual(
                        json.loads(repo.project.read_text()),
                        {"version": 1, "roles": {}, "escalate": [pattern]},
                    )


    def test_normalized_traversal_stays_rejected(self):
        for pattern in ("a/../../x", "..", " ../x "):
            with self.subTest(pattern=pattern):
                with self.open_repo() as directory:
                    repo = Repo(directory)
                    repo.put(repo.project, {"mode": "light", "escalate": [pattern]})
                    before = repo.project.read_bytes()
                    show = repo.show_bug_fix()
                    write = repo.write("--project", "--keep")
                    mode = repo.run("mode")
                    after = repo.project.read_bytes()
                message = f"error: {repo.project}: escalate pattern {pattern!r} leaves the repository\n"
                for completed in (show, write, mode):
                    self.assertEqual(completed.returncode, 1)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr, message)
                self.assertEqual(after, before)


    def test_mode_flags_print_their_help(self):
        mode_flags = (
            ("--brief-mode", "brief mode; overrides session, coordinator, project, and user modes"),
            ("--session-mode", "session mode; used after brief and before coordinator, project, and user modes"),
            ("--coordinator-mode", "coordinator mode; used after brief and session, before project and user modes"),
        )
        mode_only = (
            ("--send-backs", "SEND_BACKS", "send-back count; 2 or more forces full mode"),
            (
                "--escalated",
                "ESCALATED",
                "recorded one-line escalation reason; forces full mode and wins over paths and send-backs",
            ),
        )
        for command in ("show", "mode"):
            with self.subTest(command=command):
                completed = in_process.finished(in_process.Reason.HELP, in_process.Invocation(
                    program=ROOT / "t3/scripts/roles.py",
                    argv=(command, "--help"),
                    env={**os.environ, "COLUMNS": "200"},
                ))
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stderr, "")
                for flag, text in mode_flags:
                    block = f"  {flag} {{full,light}}\n" + (" " * 24) + text + "\n"
                    self.assertIn(block, completed.stdout)
                if command == "mode":
                    for flag, metavar, text in mode_only:
                        block = f"  {flag} {metavar}\n" + (" " * 24) + text + "\n"
                        self.assertIn(block, completed.stdout)


    def test_escalate_non_string_exits_1(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"escalate": [1]})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.project}: escalate must be a list of strings\n",
        )


    def test_bad_budget_still_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"budget": "huge"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.user}: budget 'huge' is not one of default, small, medium, large, unlimited\n",
        )


    def test_bad_mode_flag_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.write("--mode", "bogus")
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(repo.user.exists())
            self.assertFalse(repo.project.exists())


    def test_project_rewrite_drops_mode_and_keeps_escalate(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            first = repo.write("--project", "--mode", "light", "--escalate", "**/migrations/**")
            self.assertEqual(first.returncode, 0, first.stderr)
            second = repo.write("--project", "--replace", "--set", "bug-fix=inherit")
            self.assertEqual(second.returncode, 0, second.stderr)
            document = json.loads(repo.project.read_text())
            self.assertEqual(list(document), ["version", "roles", "escalate"])
            self.assertEqual(document["roles"]["bug-fix"], ["inherit"])
            self.assertEqual(document["escalate"], ["**/migrations/**"])
            self.assertNotIn("mode", document)
            self.assertNotIn("budget", document)
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "light")
        self.assertEqual(payload["modeSource"], str(repo.user))
        self.assertEqual(payload["escalate"], ["**/migrations/**"])


    def test_replace_without_mode_resets_to_light(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            first = repo.write("--mode", "full")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(repo.user.read_text())["mode"], "full")
            second = repo.write("--replace", "--budget", "small")
            self.assertEqual(second.returncode, 0, second.stderr)
            document = json.loads(repo.user.read_text())
        self.assertEqual(list(document), ["version", "roles", "budget", "mode"])
        self.assertEqual(document, {"version": 1, "roles": {}, "budget": "small", "mode": "light"})


    def test_keep_preserves_user_mode(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "full"})
            completed = repo.write("--keep", "--budget", "small")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(repo.user.read_text())
        self.assertEqual(document["mode"], "full")
        self.assertEqual(document["budget"], "small")


    def test_project_escalate_write_is_idempotent(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            args = ("--project", "--escalate", "**/migrations/**", "--escalate", "scripts/install.py")
            first = repo.write(*args)
            self.assertEqual(first.returncode, 0, first.stderr)
            written = repo.project.read_text()
            second = repo.write(*args)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(repo.project.read_text(), written)
            self.assertEqual(
                json.loads(written)["escalate"],
                ["**/migrations/**", "scripts/install.py"],
            )
            third = repo.write("--project", "--mode", "light")
            self.assertEqual(third.returncode, 0, third.stderr)
            document = json.loads(repo.project.read_text())
        self.assertEqual(document["escalate"], ["**/migrations/**", "scripts/install.py"])
        self.assertEqual(document["mode"], "light")
        self.assertEqual(list(document), ["version", "roles", "mode", "escalate"])


    def test_clear_escalate_is_idempotent(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"version": 1, "roles": {}, "mode": "light", "escalate": ["**/migrations/**"]})
            first = repo.write("--project", "--clear-escalate", "--keep")
            self.assertEqual(first.returncode, 0, first.stderr)
            document = json.loads(repo.project.read_text())
            self.assertNotIn("escalate", document)
            self.assertEqual(document["mode"], "light")
            written = repo.project.read_text()
            second = repo.write("--project", "--clear-escalate", "--keep")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(repo.project.read_text(), written)


    def test_escalate_without_project_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.write("--escalate", "**/migrations/**")
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(repo.user.exists())
            self.assertFalse(repo.project.exists())


    def test_clear_escalate_without_project_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.write("--clear-escalate")
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(repo.user.exists())
            self.assertFalse(repo.project.exists())
