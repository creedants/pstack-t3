import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

import json
import os
import shutil
import subprocess
import tempfile
from tests.pstack_t3.support import fence_git_environment, config, RUNTIME_DOCS


AUDIT = ROOT / "t3/overrides/poteto-mode/scripts/worktree-audit.sh"

def _init_repo(path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "audit@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "audit"], check=True)
    (path / "f").write_text("a\n")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)

def _add_worktree(repo, path, branch):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", str(path), "-b", branch],
        check=True,
    )

def setUpModule():
    fence_git_environment()

class WorktreeAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Git records the physical path, which the audit prints, so resolve the temporary root.
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        _init_repo(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def _audit(self, *args, **env):
        run_env = os.environ.copy()
        for key in ("T3CODE_HOME", "T3_HOME", "T3_WORKTREES"):
            run_env.pop(key, None)
        run_env.update(env)
        completed = subprocess.run(
            [str(AUDIT), str(self.repo), *args],
            capture_output=True,
            text=True,
            env=run_env,
            timeout=60,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        marks = {}
        lines = [line for line in completed.stdout.splitlines() if line]
        self.assertGreaterEqual(len(lines), 2, completed.stdout)
        for line in lines[1:]:
            cols = line.split("\t")
            self.assertEqual(len(cols), 10, line)
            marks[cols[9]] = cols[7]
        return marks

    def test_configured_locations_count_and_bad_ones_do_not(self):
        t3_home = self.root / "code-home"
        other_home = self.root / "other-home"
        default = t3_home / "worktrees" / "proj" / "wt-default"
        custom = self.root / "home" / "custom" / "wt-custom"
        previous = self.root / "old" / "wt-old"
        stray = self.root / "stray" / "wt-stray"
        _add_worktree(self.repo, default, "b-default")
        _add_worktree(self.repo, custom, "b-custom")
        _add_worktree(self.repo, previous, "b-old")
        _add_worktree(self.repo, stray, "b-stray")
        (t3_home / "userdata").mkdir(parents=True)
        (t3_home / "userdata" / "settings.json").write_text(json.dumps({
            "worktreesDirectory": "~/custom",
            "previousWorktreesDirectories": [str(previous.parent), "/", "relative/nope"],
        }))
        marks = self._audit(
            T3CODE_HOME=str(t3_home),
            T3_HOME=str(other_home),
            HOME=str(self.root / "home"),
        )
        self.assertEqual(marks[str(default)], "yes")
        self.assertEqual(marks[str(custom)], "yes")
        self.assertEqual(marks[str(previous)], "yes")
        self.assertEqual(marks[str(stray)], "no")

    def test_flag_and_env_replace_the_configured_directory(self):
        t3_home = self.root / "t3home"
        default = t3_home / "worktrees" / "proj" / "wt-default"
        chosen = self.root / "chosen" / "wt"
        decoy = self.root / "decoy" / "wt"
        _add_worktree(self.repo, default, "b-default")
        _add_worktree(self.repo, chosen, "b-chosen")
        _add_worktree(self.repo, decoy, "b-decoy")
        (t3_home / "userdata").mkdir(parents=True)
        (t3_home / "userdata" / "settings.json").write_text(json.dumps({
            "worktreesDirectory": str(decoy.parent),
        }))
        marks = self._audit(
            "--t3-worktrees", str(chosen.parent),
            T3_HOME=str(t3_home),
            T3_WORKTREES=str(decoy.parent),
        )
        self.assertEqual(marks[str(default)], "yes")
        self.assertEqual(marks[str(chosen)], "yes")
        self.assertEqual(marks[str(decoy)], "no")

    def _parser_path(self, parser):
        bindir = self.root / f"bin-{parser}"
        bindir.mkdir()
        for cmd in (
            "bash", "git", "sed", "head", "date", "awk", "mktemp", "rm",
            "sort", "du", "grep", parser,
        ):
            found = shutil.which(cmd)
            if found is None:
                self.skipTest(f"{cmd} is not installed")
            (bindir / cmd).symlink_to(found)
        return str(bindir)

    def test_symlinked_setting_matches_the_physical_worktree(self):
        t3_home = self.root / "t3home"
        custom = self.root / "custom"
        alias = self.root / "alias"
        root_link = self.root / "root-link"
        direct = custom / "wt"
        via_alias = alias / "created-via-alias"
        outside = self.root / "outside" / "wt"
        default = t3_home / "worktrees" / "wt"
        _add_worktree(self.repo, direct, "b-direct")
        alias.symlink_to(custom, target_is_directory=True)
        _add_worktree(self.repo, via_alias, "b-alias")
        _add_worktree(self.repo, outside, "b-out")
        _add_worktree(self.repo, default, "b-default")
        root_link.symlink_to(Path("/"))
        (t3_home / "userdata").mkdir(parents=True)
        settings = t3_home / "userdata" / "settings.json"
        settings.write_text(json.dumps({"worktreesDirectory": str(alias)}))
        marks = self._audit(T3CODE_HOME=str(t3_home))
        self.assertEqual(marks[str(direct)], "yes")
        self.assertEqual(marks[str(custom / "created-via-alias")], "yes")
        self.assertEqual(marks[str(default)], "yes")
        self.assertEqual(marks[str(outside)], "no")

        settings.write_text(json.dumps({"worktreesDirectory": str(root_link)}))
        marks = self._audit(T3CODE_HOME=str(t3_home))
        self.assertEqual(marks[str(outside)], "no")
        self.assertEqual(marks[str(default)], "yes")
        self.assertEqual(marks[str(direct)], "no")

    def test_bad_previous_field_is_ignored_by_each_parser(self):
        t3_home = self.root / "t3home"
        custom = self.root / "custom" / "wt"
        outside = self.root / "outside" / "wt"
        previous = self.root / "previous" / "wt"
        default = t3_home / "worktrees" / "wt"
        for path, branch in (
            (custom, "b-custom"),
            (outside, "b-out"),
            (previous, "b-prev"),
            (default, "b-default"),
        ):
            _add_worktree(self.repo, path, branch)
        (t3_home / "userdata").mkdir(parents=True)
        settings = t3_home / "userdata" / "settings.json"
        bad_values = (
            {"oops": str(outside.parent)},
            "invalid",
            12,
            None,
            [1, {"oops": str(outside.parent)}, str(previous.parent)],
        )
        parsers = [name for name in ("jq", "python3") if shutil.which(name)]
        self.assertGreaterEqual(len(parsers), 1)
        for parser in parsers:
            tool_path = self._parser_path(parser)
            for value in bad_values:
                settings.write_text(json.dumps({
                    "worktreesDirectory": str(custom.parent),
                    "previousWorktreesDirectories": value,
                }))
                marks = self._audit(T3CODE_HOME=str(t3_home), PATH=tool_path)
                self.assertEqual(marks[str(custom)], "yes", (parser, value))
                self.assertEqual(marks[str(outside)], "no", (parser, value))
                self.assertEqual(marks[str(default)], "yes", (parser, value))
                expect_previous = "yes" if isinstance(value, list) else "no"
                self.assertEqual(marks[str(previous)], expect_previous, (parser, value))

    def test_padded_home_and_setting_count(self):
        t3_home = self.root / "t3home"
        home = self.root / "home"
        custom = self.root / "custom" / "wt"
        tilde_custom = home / "custom" / "wt"
        outside = self.root / "outside" / "wt"
        default = t3_home / "worktrees" / "wt"
        _add_worktree(self.repo, custom, "b-custom")
        _add_worktree(self.repo, tilde_custom, "b-tilde")
        _add_worktree(self.repo, outside, "b-out")
        _add_worktree(self.repo, default, "b-default")
        (t3_home / "userdata").mkdir(parents=True)
        settings = t3_home / "userdata" / "settings.json"
        settings.write_text(json.dumps({
            "worktreesDirectory": f"  {custom.parent}  ",
        }))
        marks = self._audit(T3CODE_HOME=f"  {t3_home}  ", HOME=str(home))
        self.assertEqual(marks[str(default)], "yes")
        self.assertEqual(marks[str(custom)], "yes")
        self.assertEqual(marks[str(outside)], "no")

        settings.write_text(json.dumps({"worktreesDirectory": "  ~/custom  "}))
        marks = self._audit(T3CODE_HOME=f"  {t3_home}  ", HOME=str(home))
        self.assertEqual(marks[str(tilde_custom)], "yes")
        self.assertEqual(marks[str(custom)], "no")

        marks = self._audit(T3CODE_HOME="   ", T3_HOME=str(t3_home), HOME=str(home))
        self.assertEqual(marks[str(default)], "yes")

    def test_javascript_whitespace_pad_counts_for_each_parser(self):
        t3_home = self.root / "t3home"
        custom = self.root / "custom" / "wt"
        outside = self.root / "outside" / "wt"
        default = t3_home / "worktrees" / "wt"
        _add_worktree(self.repo, custom, "b-custom")
        _add_worktree(self.repo, outside, "b-out")
        _add_worktree(self.repo, default, "b-default")
        (t3_home / "userdata").mkdir(parents=True)
        settings = t3_home / "userdata" / "settings.json"
        parsers = [name for name in ("jq", "python3") if shutil.which(name)]
        self.assertEqual(parsers, ["jq", "python3"])
        paths = {parser: self._parser_path(parser) for parser in parsers}
        for pad in ("\u00a0", "\ufeff"):
            for parser, tool_path in paths.items():
                label = (parser, hex(ord(pad)))
                settings.write_text(json.dumps({
                    "worktreesDirectory": f"{pad}{custom.parent}{pad}",
                }))
                marks = self._audit(T3CODE_HOME=str(t3_home), PATH=tool_path)
                self.assertEqual(marks[str(custom)], "yes", (*label, "setting"))
                self.assertEqual(marks[str(default)], "yes", (*label, "setting"))
                self.assertEqual(marks[str(outside)], "no", (*label, "setting"))

                settings.write_text(json.dumps({
                    "worktreesDirectory": str(custom.parent),
                }))
                marks = self._audit(
                    T3CODE_HOME=f"{pad}{t3_home}{pad}",
                    PATH=tool_path,
                )
                self.assertEqual(marks[str(custom)], "yes", (*label, "home"))
                self.assertEqual(marks[str(default)], "yes", (*label, "home"))
                self.assertEqual(marks[str(outside)], "no", (*label, "home"))

    def test_playbook_names_the_configured_worktree_location(self):
        text = (ROOT / "t3/overrides/poteto-mode/playbooks/worktree-cleanup.md").read_text()
        self.assertIn("worktreesDirectory", text)
        self.assertIn("--t3-worktrees", text)
        self.assertIn("T3_WORKTREES", text)
        self.assertIn("replaces those settings roots", text)


# The runtime docs join in RUNTIME_DOCS order. Later items append to this tuple.
