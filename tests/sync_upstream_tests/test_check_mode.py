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
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from .support import BADGE_LINE_1_0_0, GIT_ENV, LAYERS, PINNED_TREE, PLUGIN, RENDERED_FROM, ROOT, CheckoutCase, build, commit, git, last_line, setUpModule, snapshot, sync_upstream

class CheckTest(CheckoutCase):
    def check(self, *flags, **environment):
        return self.sync("--check", *flags, **environment)

    def rows(self, files, version="1.0.0"):
        head = commit(self.upstream, files)
        result = self.check()
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        header, *rows = result.stdout.splitlines()
        self.assertEqual(header, f"upstream main: {self.pinned[:12]} -> {head[:12]}{version and f' ({version})'}")
        return rows

    def failure(self, *flags, **environment):
        result = self.check(*flags, **environment)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        return result.stderr

    def test_up_to_date(self):
        result = self.check()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, f"up to date ({self.pinned[:12]})\n", ""))

    def test_a_changed_overridden_file(self):
        self.assertEqual(
            self.rows({"pstack/skills/alpha/SKILL.md": "edited\n"}),
            ["changed  pstack/skills/alpha/SKILL.md  overridden by t3/overrides/alpha/SKILL.md"],
        )

    def test_a_changed_file_that_ships_unchanged(self):
        self.assertEqual(
            self.rows({"pstack/skills/beta/SKILL.md": "edited\n"}),
            ["changed  pstack/skills/beta/SKILL.md  ships unchanged"],
        )

    def test_an_added_file(self):
        self.assertEqual(
            self.rows({"pstack/skills/beta/new.md": "added\n"}),
            ["added    pstack/skills/beta/new.md  ships unchanged"],
        )

    def test_a_removed_file(self):
        self.assertEqual(
            self.rows({"pstack/skills/beta/old.md": None}),
            ["removed  pstack/skills/beta/old.md  ships unchanged"],
        )

    def test_a_path_under_a_directory_in_removed_txt(self):
        self.assertEqual(
            self.rows({"pstack/skills/gone/deep/file.md": "edited\n", "pstack/skills/gone/new.md": "added\n"}),
            [
                "changed  pstack/skills/gone/deep/file.md  dropped by t3/removed.txt",
                "added    pstack/skills/gone/new.md  dropped by t3/removed.txt",
            ],
        )

    def test_paths_that_never_ship_and_paths_ported_by_hand(self):
        self.assertEqual(
            self.rows(
                {
                    "pstack/README.md": "edited\n",
                    "pstack/agents/persona.md": "edited\n",
                    "pstack/agents/unported.md": "added\n",
                    "pstack/automations/nightly/skills/job.md": "edited\n",
                    "pstack/skills/setup-pstack/SKILL.md": "edited\n",
                }
            ),
            [
                "changed  pstack/README.md  not shipped",
                "changed  pstack/agents/persona.md  overridden by t3/agents/persona.md",
                "added    pstack/agents/unported.md  not shipped",
                "changed  pstack/automations/nightly/skills/job.md  not shipped",
                "changed  pstack/skills/setup-pstack/SKILL.md  overridden by t3/setup.md",
            ],
        )

    def test_each_row_names_the_file_the_build_renders(self):
        commit(self.upstream, {f"pstack/skills/{key}": "edited upstream\n" for key in RENDERED_FROM})
        report = json.loads(self.check("--json").stdout)
        claimed = {
            entry["path"][len("pstack/skills/") :]: {
                "overridden": entry["t3_path"],
                "unchanged": f"vendor/{entry['path']}",
                "dropped": None,
            }[entry["handling"]]
            for entry in report["changes"]
        }
        self.assertEqual(claimed, RENDERED_FROM)
        rendered = self.directory / "rendered"
        subprocess.run(
            [sys.executable, "-B", "-c", "import pathlib, sys, build; build.render(pathlib.Path(sys.argv[1]))", str(rendered)],
            cwd=self.root / "scripts",
            check=True,
        )
        for key, source in RENDERED_FROM.items():
            with self.subTest(path=key):
                if source is None:
                    self.assertFalse((rendered / key).exists())
                else:
                    self.assertEqual(last_line(rendered / key), last_line(self.root / source))

    def test_a_commit_that_leaves_the_prefix_alone(self):
        self.assertEqual(self.rows({"other/notes.md": "edited\n"}), ["no changes under pstack/"])
        report = json.loads(self.check("--json").stdout)
        self.assertEqual((report["up_to_date"], report["changes"]), (False, []))

    def test_a_filename_with_a_space_and_a_newline_stays_one_row(self):
        name = "pstack/skills/beta/odd name\nadded    pstack/skills/alpha/SKILL.md  ships unchanged"
        self.assertEqual(self.rows({name: "added\n"}), [f"added    {json.dumps(name)}  ships unchanged"])
        self.assertEqual(json.loads(self.check("--json").stdout)["changes"][0]["path"], name)

    def test_a_filename_with_a_space_is_quoted(self):
        self.assertEqual(
            self.rows({"pstack/skills/beta/two words.md": "added\n"}),
            ['added    "pstack/skills/beta/two words.md"  ships unchanged'],
        )

    def test_a_fixed_copy_missing_from_t3_overrides_nothing(self):
        (self.root / "t3/setup.md").unlink()
        self.assertEqual(
            self.rows({"pstack/skills/setup-pstack/SKILL.md": "edited\n"}),
            ["changed  pstack/skills/setup-pstack/SKILL.md  ships unchanged"],
        )

    def test_the_header_names_the_new_version_when_upstream_states_one(self):
        self.assertEqual(
            self.rows({PLUGIN: '{"name": "pstack", "version": "1.1.0"}\n'}, version="1.1.0"),
            [f"changed  {PLUGIN}  not shipped"],
        )
        self.assertEqual(self.rows({PLUGIN: None}, version=""), [f"removed  {PLUGIN}  not shipped"])

    def test_json_holds_the_same_report(self):
        up_to_date = {
            "repository": str(self.upstream),
            "ref": "main",
            "pinned": self.pinned,
            "commit": self.pinned,
            "version": "1.0.0",
            "up_to_date": True,
            "changes": [],
        }
        result = self.check("--json")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(json.loads(result.stdout), up_to_date)
        head = commit(
            self.upstream,
            {
                PLUGIN: '{"name": "pstack", "version": "1.1.0"}\n',
                "pstack/skills/alpha/SKILL.md": "edited\n",
                "pstack/skills/beta/new.md": "added\n",
                "pstack/skills/beta/old.md": None,
                "pstack/skills/gone/deep/file.md": "edited\n",
            },
        )
        result = self.check("--json", "--ref", head)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(result.stdout[-2:], "}\n")
        self.assertEqual(
            json.loads(result.stdout),
            {
                **up_to_date,
                "ref": head,
                "commit": head,
                "version": "1.1.0",
                "up_to_date": False,
                "changes": [
                    {"path": PLUGIN, "change": "changed", "handling": "not-shipped", "t3_path": None},
                    {
                        "path": "pstack/skills/alpha/SKILL.md",
                        "change": "changed",
                        "handling": "overridden",
                        "t3_path": "t3/overrides/alpha/SKILL.md",
                    },
                    {"path": "pstack/skills/beta/new.md", "change": "added", "handling": "unchanged", "t3_path": None},
                    {"path": "pstack/skills/beta/old.md", "change": "removed", "handling": "unchanged", "t3_path": None},
                    {
                        "path": "pstack/skills/gone/deep/file.md",
                        "change": "changed",
                        "handling": "dropped",
                        "t3_path": None,
                    },
                ],
            },
        )

    def test_the_repository_flag_replaces_the_one_in_upstream_json(self):
        commit(self.upstream, {"pstack/README.md": "edited\n"})
        elsewhere = self.directory / "elsewhere"
        meta = json.loads((self.root / "upstream.json").read_text())
        (self.root / "upstream.json").write_text(json.dumps({**meta, "repository": str(elsewhere)}))
        self.assertIn(f"repository '{elsewhere}' does not exist", self.failure())
        result = self.check("--repository", str(self.upstream), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report["repository"], report["changes"][0]["path"]), (str(self.upstream), "pstack/README.md"))

    def test_an_unknown_ref_exits_1_with_the_git_error(self):
        self.assertEqual(self.failure("--ref", "nope"), "fatal: Needed a single revision\n")

    def test_a_pinned_commit_upstream_lacks_exits_1_with_the_git_error(self):
        commit(self.upstream, {"pstack/README.md": "edited\n"})
        meta = json.loads((self.root / "upstream.json").read_text())
        (self.root / "upstream.json").write_text(json.dumps({**meta, "commit": "1" * 40}))
        self.assertIn(f"fatal: bad object {'1' * 40}", self.failure())

    def test_a_missing_git_exits_1(self):
        empty = self.directory / "empty"
        empty.mkdir()
        self.assertEqual(self.failure(PATH=str(empty)), "[Errno 2] No such file or directory: 'git'\n")

    def test_json_or_repository_without_check_is_a_usage_error_that_syncs_nothing(self):
        commit(self.upstream, {"pstack/README.md": "edited\n"})
        before = snapshot(self.root)
        for flags in (("--json",), ("--repository", str(self.upstream))):
            with self.subTest(flags=flags):
                result = self.sync(*flags)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--json and --repository need --check", result.stderr)
                self.assertEqual(snapshot(self.root), before)

    def test_writes_nothing(self):
        commit(self.upstream, {"pstack/skills/alpha/SKILL.md": "edited\n"})
        git(self.root, "init", "--quiet")
        git(self.root, "add", "--all")
        git(self.root, "commit", "--quiet", "--message", "checkout")
        before = snapshot(self.root)
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(self.check("--json").returncode, 0)
        self.assertEqual(self.check("--ref", self.pinned).returncode, 0)
        self.assertEqual(self.check("--ref", "nope").returncode, 1)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(list(self.root.rglob("__pycache__")), [])
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertEqual(git(self.root, "status", "--porcelain", "--ignored"), "")


