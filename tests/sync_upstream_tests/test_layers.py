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
from .support import CLASSIFIED, GIT_ENV, LAYERS, PINNED_TREE, PLUGIN, ROOT, build, commit, git, new_upstream, setUpModule, sync_upstream

class ClassifyTest(unittest.TestCase):
    def test_each_path_gets_the_layer_the_build_ships(self):
        for path, expected in CLASSIFIED:
            with self.subTest(path=path):
                self.assertEqual(sync_upstream.classify(path, "pstack", LAYERS, ()), expected)

    def test_the_prefix_can_be_nested(self):
        self.assertEqual(
            sync_upstream.classify("plugins/pstack/skills/alpha/SKILL.md", "plugins/pstack", LAYERS, ()),
            ("overridden", "t3/overrides/alpha/SKILL.md"),
        )

    def test_agrees_with_the_real_build(self):
        layers = sync_upstream.load_layers()
        skills = ROOT / "vendor/pstack/skills"
        seen = set()
        with tempfile.TemporaryDirectory() as directory:
            rendered = Path(directory)
            build.render(rendered)
            for rel in build.relative_files(skills):
                handling, t3_path = sync_upstream.classify(f"pstack/skills/{rel.as_posix()}", "pstack", layers, ())
                seen.add(handling)
                with self.subTest(path=rel.as_posix()):
                    self.assertIn(handling, ("overridden", "unchanged", "dropped"))
                    self.assertEqual((rendered / rel).is_file(), handling != "dropped")
                    if handling == "dropped" or (len(rel.parts) == 2 and rel.name == "SKILL.md"):
                        continue
                    source = ROOT / t3_path if handling == "overridden" else skills / rel
                    self.assertEqual((rendered / rel).read_bytes(), source.read_bytes())
        self.assertLessEqual({"overridden", "unchanged"}, seen)


class UpstreamChangesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.upstream, self.pinned = new_upstream(temporary.name)
        environment = mock.patch.dict(os.environ, GIT_ENV, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def changes(self, repository=None, ref="main", prefix="pstack"):
        return sync_upstream.upstream_changes(repository or str(self.upstream), ref, self.pinned, prefix, ())

    def test_the_pinned_commit_has_no_changes(self):
        self.assertEqual(self.changes(), (self.pinned, "1.0.0", []))

    def test_lists_what_changed_under_the_prefix_since_the_pinned_commit(self):
        head = commit(
            self.upstream,
            {
                "other/notes.md": "edited outside the prefix\n",
                "pstack-extra/skills/alpha/SKILL.md": "a sibling that shares the prefix's first letters\n",
                PLUGIN: '{"name": "pstack", "version": "1.1.0"}\n',
                "pstack/skills/alpha/SKILL.md": "edited\n",
                "pstack/skills/beta/odd name\n.md": "added\n",
                "pstack/skills/beta/old.md": None,
            },
        )
        self.assertEqual(
            self.changes(),
            (
                head,
                "1.1.0",
                [
                    ("changed", PLUGIN),
                    ("changed", "pstack/skills/alpha/SKILL.md"),
                    ("added", "pstack/skills/beta/odd name\n.md"),
                    ("removed", "pstack/skills/beta/old.md"),
                ],
            ),
        )

    def test_a_rename_is_one_removed_path_and_one_added_path(self):
        head = commit(
            self.upstream,
            {"pstack/skills/beta/old.md": None, "pstack/skills/beta/renamed.md": PINNED_TREE["pstack/skills/beta/old.md"]},
        )
        self.assertEqual(
            self.changes(),
            (head, "1.0.0", [("removed", "pstack/skills/beta/old.md"), ("added", "pstack/skills/beta/renamed.md")]),
        )

    def test_a_commit_that_leaves_the_prefix_alone_has_no_changes(self):
        head = commit(self.upstream, {"other/notes.md": "edited outside the prefix\n"})
        self.assertEqual(self.changes(), (head, "1.0.0", []))

    def test_a_glob_character_in_the_prefix_matches_only_itself(self):
        head = commit(self.upstream, {"p*/skills/alpha/SKILL.md": "added\n", "pstack/README.md": "edited\n"})
        self.assertEqual(self.changes(prefix="p*"), (head, None, [("added", "p*/skills/alpha/SKILL.md")]))

    def test_a_tag_and_a_commit_resolve_like_a_branch(self):
        head = commit(self.upstream, {"pstack/README.md": "edited\n"})
        git(self.upstream, "tag", "--annotate", "--message", "release", "v2")
        commit(self.upstream, {"pstack/README.md": "edited again\n"})
        expected = (head, "1.0.0", [("changed", "pstack/README.md")])
        self.assertEqual(self.changes(ref="v2"), expected)
        self.assertEqual(self.changes(ref=head), expected)

    def test_a_blobless_clone_still_reads_the_version(self):
        git(self.upstream, "config", "uploadpack.allowFilter", "true")
        head = commit(self.upstream, {PLUGIN: '{"name": "pstack", "version": "2.0.0"}\n'})
        self.assertEqual(self.changes(repository=self.upstream.as_uri()), (head, "2.0.0", [("changed", PLUGIN)]))

    def test_an_unreadable_version_is_none(self):
        for text, change in (("not json\n", "changed"), ("[]\n", "changed"), ('{"version": 2}\n', "changed"), (None, "removed")):
            with self.subTest(plugin=text):
                head = commit(self.upstream, {PLUGIN: text})
                self.assertEqual(self.changes(), (head, None, [(change, PLUGIN)]))


