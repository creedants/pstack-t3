"""What sync_upstream.py reads from an upstream repository the test builds, with no network."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build  # noqa: E402
import sync_upstream  # noqa: E402

# A developer's git config, identity, and locale must not change a result.
GIT_ENV = {
    **{name: value for name, value in os.environ.items() if not name.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "LC_ALL": "C",
}
PLUGIN = "pstack/.cursor-plugin/plugin.json"
PINNED_TREE = {
    "other/notes.md": "upstream other/notes.md\n",
    PLUGIN: '{"name": "pstack", "version": "1.0.0"}\n',
    "pstack/README.md": "upstream README.md\n",
    "pstack/agents/persona.md": "upstream agents/persona.md\n",
    "pstack/automations/nightly/skills/job.md": "upstream automations job.md\n",
    "pstack/skills/alpha/SKILL.md": "upstream alpha/SKILL.md\n",
    "pstack/skills/beta/SKILL.md": "upstream beta/SKILL.md\n",
    "pstack/skills/beta/old.md": "upstream beta/old.md\n",
    "pstack/skills/gone/deep/file.md": "upstream gone/deep/file.md\n",
}
LAYERS = sync_upstream.Layers(
    hand_ported={"agents/persona.md": "t3/agents/persona.md", "skills/setup-pstack/SKILL.md": "t3/setup.md"},
    added=frozenset({"alpha/both.md", "setup-pstack/SKILL.md"}),
    overrides=frozenset({"alpha/SKILL.md", "alpha/both.md", "gone/kept.md"}),
    removed=frozenset({"gone", "beta/old.md"}),
)
CLASSIFIED = (
    ("pstack/skills/alpha/SKILL.md", ("overridden", "t3/overrides/alpha/SKILL.md")),
    ("pstack/skills/alpha/both.md", ("overridden", "t3/added/alpha/both.md")),
    ("pstack/skills/gone/kept.md", ("overridden", "t3/overrides/gone/kept.md")),
    ("pstack/skills/setup-pstack/SKILL.md", ("overridden", "t3/setup.md")),
    ("pstack/agents/persona.md", ("overridden", "t3/agents/persona.md")),
    ("pstack/skills/gone/deep/file.md", ("dropped", None)),
    ("pstack/skills/beta/old.md", ("dropped", None)),
    ("pstack/skills/beta/SKILL.md", ("unchanged", None)),
    ("pstack/skills/gone-later/file.md", ("unchanged", None)),
    ("pstack/skills/top-level.md", ("unchanged", None)),
    ("pstack/agents/unported.md", ("not-shipped", None)),
    ("pstack/README.md", ("not-shipped", None)),
    ("pstack/automations/nightly/skills/alpha/SKILL.md", ("not-shipped", None)),
    ("pstack/skills/alpha/__pycache__/SKILL.md", ("not-shipped", None)),
    ("pstack/skills", ("not-shipped", None)),
)


def git(directory, *arguments):
    return subprocess.run(
        ("git", *arguments), cwd=directory, env=GIT_ENV, check=True, capture_output=True, text=True
    ).stdout.strip()


def write_tree(base, files):
    for name, text in files.items():
        target = base / name
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def commit(repository, files):
    """Write each file, delete the ones mapped to None, and return the new commit."""
    write_tree(repository, files)
    git(repository, "add", "--all")
    git(repository, "commit", "--quiet", "--message", "change")
    return git(repository, "rev-parse", "HEAD")


def new_upstream(directory):
    repository = Path(directory) / "upstream"
    repository.mkdir()
    git(repository, "init", "--quiet", "--initial-branch", "main")
    return repository, commit(repository, PINNED_TREE)


class ClassifyTest(unittest.TestCase):
    def test_each_path_gets_the_layer_the_build_ships(self):
        for path, expected in CLASSIFIED:
            with self.subTest(path=path):
                self.assertEqual(sync_upstream.classify(path, "pstack", LAYERS), expected)

    def test_the_prefix_can_be_nested(self):
        self.assertEqual(
            sync_upstream.classify("plugins/pstack/skills/alpha/SKILL.md", "plugins/pstack", LAYERS),
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
                handling, t3_path = sync_upstream.classify(f"pstack/skills/{rel.as_posix()}", "pstack", layers)
                seen.add(handling)
                with self.subTest(path=rel.as_posix()):
                    self.assertIn(handling, ("overridden", "unchanged", "dropped"))
                    self.assertEqual((rendered / rel).is_file(), handling != "dropped")
                    # render() rewrites each skill's SKILL.md, so only its presence can be compared.
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
        return sync_upstream.upstream_changes(repository or str(self.upstream), ref, self.pinned, prefix)

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


if __name__ == "__main__":
    unittest.main()
