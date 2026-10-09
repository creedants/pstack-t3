"""Installer behavior a fresh home can observe."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("alpha", "pstack-runtime", "swarm")


def run_install(home, *args):
    env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/install.py"), *args],
        env=env,
        capture_output=True,
        text=True,
    )


class FreshInstallTest(unittest.TestCase):
    def test_fresh_install_links_every_skill_and_rerun_adds_no_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            names = sorted(p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").is_file())
            first = run_install(home)
            self.assertEqual(first.returncode, 0, first.stderr)
            for harness in (".claude", ".agents", ".grok", ".cursor"):
                for name in names:
                    link = home / harness / "skills" / name
                    self.assertTrue(link.is_symlink(), link)
                    self.assertEqual(link.resolve(), (ROOT / "skills" / name).resolve(), link)
                    self.assertEqual((link / "SKILL.md").read_bytes(), (ROOT / "skills" / name / "SKILL.md").read_bytes())
            backups = home / ".config/pstack-t3/backups"
            self.assertFalse(backups.exists(), "a fresh install created a backup directory")
            again = run_install(home)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("already installed", again.stdout)
            self.assertFalse(backups.exists(), "rerunning install created a backup directory")

    def test_replace_saves_the_old_skill_and_rerun_adds_no_empty_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            swarm = home / ".grok/skills/swarm"
            swarm.mkdir(parents=True)
            (swarm / "SKILL.md").write_text("foreign-skill\n")
            refused = run_install(home, "--harness", "grok")
            self.assertNotEqual(refused.returncode, 0)
            self.assertFalse((home / ".grok/skills/poteto-mode").exists())
            replaced = run_install(home, "--harness", "grok", "--replace")
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            saved = list((home / ".config/pstack-t3/backups").rglob("SKILL.md"))
            self.assertEqual([path.read_text() for path in saved], ["foreign-skill\n"])
            stamps = sorted(p.name for p in (home / ".config/pstack-t3/backups").iterdir() if p.is_dir())
            self.assertEqual(len(stamps), 1)
            again = run_install(home, "--harness", "grok")
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("already installed", again.stdout)
            stamps_after = sorted(p.name for p in (home / ".config/pstack-t3/backups").iterdir() if p.is_dir())
            self.assertEqual(stamps_after, stamps)
            removed = run_install(home, "--harness", "grok", "uninstall")
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertEqual((swarm / "SKILL.md").read_text(), "foreign-skill\n")
            self.assertFalse((home / ".grok/skills/poteto-mode").exists())


def fresh_home():
    wrapper = tempfile.TemporaryDirectory()
    return wrapper, Path(os.path.realpath(wrapper.name))


def provider_link(home, harness, name):
    folder = {"claude": ".claude", "codex": ".agents", "grok": ".grok", "cursor": ".cursor"}[harness]
    return home / folder / "skills" / name


def state_dir(home):
    return home / ".config" / "pstack-t3"


def v2_file(home):
    return state_dir(home) / "install-manifest-v2.json"


def legacy_file(home):
    return state_dir(home) / "install-manifest.json"


def make_checkout(home, name):
    checkout = home / "checkouts" / name
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "install.py", checkout / "scripts" / "install.py")
    for skill in NAMES:
        directory = checkout / "skills" / skill
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"{name}-{skill}\n")
    return checkout


def run(home, checkout, *args):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run(
        [sys.executable, str(checkout / "scripts" / "install.py"), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def snapshot(root):
    records = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames):
            path = Path(dirpath) / name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                records.append((rel, "link", os.readlink(path)))
            else:
                records.append((rel, "dir", None))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                records.append((rel, "link", os.readlink(path)))
            elif path.is_file():
                records.append((rel, "file", path.read_bytes()))
    return tuple(records)


def read_v2(home):
    return json.loads(v2_file(home).read_text())


def link_pairs(home):
    return {(row["path"], row["checkout"]) for row in read_v2(home)["links"]}


def grok_pairs(home, checkout):
    return {(str(provider_link(home, "grok", name)), str(checkout)) for name in NAMES}


def write_v2(home, links, backups):
    file = v2_file(home)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({"links": links, "backups": backups}, indent=2) + "\n")


def plant_links(home, checkout, harness="grok"):
    for name in NAMES:
        link = provider_link(home, harness, name)
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        os.symlink(checkout / "skills" / name, link)


class OwnershipTest(unittest.TestCase):
    def setUp(self):
        self.use_fresh()

    def use_fresh(self):
        wrapper, self.home = fresh_home()
        self.addCleanup(wrapper.cleanup)

    def ok(self, result, *lines):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        got = result.stdout.splitlines()
        for line in lines:
            self.assertIn(line, got)

    def assert_no_manifest(self):
        self.assertFalse(v2_file(self.home).exists())
        self.assertFalse(legacy_file(self.home).exists())

    def assert_grok_text(self, checkout):
        for name in NAMES:
            self.assertEqual(os.readlink(provider_link(self.home, "grok", name)), str(checkout / "skills" / name))

    def assert_grok_gone(self):
        for name in NAMES:
            self.assertFalse(os.path.lexists(provider_link(self.home, "grok", name)))

    def two(self):
        return make_checkout(self.home, "a"), make_checkout(self.home, "b")

    def base(self):
        a, b = self.two()
        self.ok(run(self.home, a, "--harness", "grok"))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        return a, b

    def test_row_1_replaced_owner_stays_tracked(self):
        a, b = self.base()
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assertEqual(link_pairs(self.home), grok_pairs(self.home, a))
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_gone()
        self.assert_no_manifest()

    def test_row_1b_owner_withdraws_links_another_checkout_moved_aside(self):
        a, b = self.base()
        self.ok(
            run(self.home, a, "--harness", "grok", "uninstall"),
            "removed 3 links, restored 0 entries",
            "3 of them had been moved aside by another checkout's --replace",
        )
        self.assert_grok_text(b)
        self.assertEqual(link_pairs(self.home), grok_pairs(self.home, b))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_gone()
        self.assert_no_manifest()

    def repoint_swarm(self, target):
        link = provider_link(self.home, "grok", "swarm")
        link.unlink()
        os.symlink(target, link)
        return str(target)

    def assert_repoint_survives(self, a, text):
        link = provider_link(self.home, "grok", "swarm")
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 2 links, restored 0 entries")
        self.assertEqual(os.readlink(link), text)
        self.assert_no_manifest()
        before = snapshot(self.home)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(os.readlink(link), text)

    def test_row_2_repointed_link_to_an_existing_directory_is_left_alone(self):
        a, b = self.base()
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        target = self.home / "unrelated-dir"
        target.mkdir()
        text = self.repoint_swarm(target)
        self.assert_repoint_survives(a, text)

    def test_row_3_repointed_dangling_link_is_left_alone(self):
        a, b = self.base()
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        text = self.repoint_swarm(self.home / "missing-target")
        self.assert_repoint_survives(a, text)

    def foreign_then_both_uninstall(self, target):
        a, b = self.two()
        link = provider_link(self.home, "grok", "swarm")
        link.parent.mkdir(parents=True)
        os.symlink(target, link)
        text = str(target)
        self.ok(run(self.home, a, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
        self.assertEqual(os.readlink(link), text)
        for name in ("alpha", "pstack-runtime"):
            self.assertFalse(os.path.lexists(provider_link(self.home, "grok", name)))
        self.assert_no_manifest()
        before = snapshot(self.home)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertEqual(snapshot(self.home), before)

    def test_row_4_foreign_symlink_comes_back_untracked(self):
        target = self.home / "foreign-target"
        target.mkdir()
        self.foreign_then_both_uninstall(target)

    def test_row_5_repointed_lookalike_link_is_left_alone(self):
        a, b = self.base()
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        lookalike = self.home / "lookalike"
        (lookalike / "scripts").mkdir(parents=True)
        (lookalike / "scripts" / "install.py").write_text("print('unrelated installer')\n")
        (lookalike / "skills" / "swarm").mkdir(parents=True)
        text = self.repoint_swarm(lookalike / "skills" / "swarm")
        self.assert_repoint_survives(a, text)
        self.assertEqual((lookalike / "scripts" / "install.py").read_text(), "print('unrelated installer')\n")

    def test_row_6_lookalike_symlink_comes_back_untracked(self):
        lookalike = self.home / "lookalike"
        (lookalike / "scripts").mkdir(parents=True)
        (lookalike / "scripts" / "install.py").write_text("print('unrelated installer')\n")
        (lookalike / "skills" / "swarm").mkdir(parents=True)
        self.foreign_then_both_uninstall(lookalike / "skills" / "swarm")

    def alias_of(self, checkout):
        alias = self.home / "a-alias"
        os.symlink(checkout, alias)
        return alias

    def test_row_7_alias_spelled_link_stays_untracked(self):
        a, b = self.two()
        alias = self.alias_of(a)
        self.ok(run(self.home, a, "--harness", "grok"))
        link = provider_link(self.home, "grok", "swarm")
        link.unlink()
        text = str(alias / "skills" / "swarm")
        os.symlink(text, link)
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.ok(run(self.home, alias, "--harness", "grok", "uninstall"), "removed 2 links, restored 0 entries")
        self.assertEqual(os.readlink(link), text)
        self.assert_no_manifest()
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertEqual(os.readlink(link), text)

    def test_row_7u_legacy_rows_do_not_adopt_an_alias_spelling(self):
        a, b = self.two()
        alias = self.alias_of(a)
        self.ok(run(self.home, a, "--harness", "grok"))
        data = read_v2(self.home)
        legacy = {"links": [{"harnesses": row["harnesses"], "path": row["path"]} for row in data["links"]], "backups": []}
        v2_file(self.home).unlink()
        legacy_file(self.home).write_text(json.dumps(legacy) + "\n")
        link = provider_link(self.home, "grok", "swarm")
        link.unlink()
        text = str(alias / "skills" / "swarm")
        os.symlink(text, link)
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.ok(run(self.home, alias, "--harness", "grok", "uninstall"), "removed 2 links, restored 0 entries")
        self.assertEqual(os.readlink(link), text)
        self.assert_no_manifest()
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertEqual(os.readlink(link), text)

    def test_row_8_relative_link_text_is_restored_exactly(self):
        a, b = self.two()
        self.ok(run(self.home, a, "--harness", "grok"))
        expected = {}
        for name in NAMES:
            link = provider_link(self.home, "grok", name)
            relative = os.path.relpath(os.readlink(link), link.parent)
            link.unlink()
            os.symlink(relative, link)
            expected[name] = relative
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        for name in NAMES:
            self.assertEqual(os.readlink(provider_link(self.home, "grok", name)), expected[name])
        self.assertEqual(link_pairs(self.home), grok_pairs(self.home, a))
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_gone()
        self.assert_no_manifest()

    def test_row_9_alias_invocation_records_the_canonical_checkout(self):
        a, b = self.two()
        alias = self.alias_of(a)
        self.ok(run(self.home, alias, "--harness", "grok"))
        self.assert_grok_text(a)
        self.assertEqual(link_pairs(self.home), grok_pairs(self.home, a))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assertEqual(link_pairs(self.home), grok_pairs(self.home, a))
        self.ok(run(self.home, alias, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_gone()
        self.assert_no_manifest()

    def share_skills(self, source, dest):
        shutil.rmtree(dest / "skills")
        os.symlink(source / "skills", dest / "skills")

    def assert_shared_tree(self, installer, other):
        self.ok(run(self.home, installer, "--harness", "grok"))
        self.ok(run(self.home, other, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assert_grok_text(installer)
        self.assertEqual(link_pairs(self.home), grok_pairs(self.home, installer))
        self.ok(run(self.home, installer, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_gone()
        self.assert_no_manifest()

    def test_row_10_forward_shared_skills_tree(self):
        a, b = self.two()
        self.share_skills(a, b)
        self.assert_shared_tree(b, a)

    def test_row_10_reverse_shared_skills_tree(self):
        a, b = self.two()
        self.share_skills(b, a)
        self.assert_shared_tree(b, a)

    def test_row_11a_deleted_checkout_links_stay_and_the_other_owner_remains(self):
        a, b = self.two()
        later = make_checkout(self.home, "l")
        self.ok(run(self.home, later, "--harness", "codex"))
        self.ok(run(self.home, a, "--harness", "grok"))
        texts = {name: os.readlink(provider_link(self.home, "grok", name)) for name in NAMES}
        shutil.rmtree(a)
        self.ok(run(self.home, b, "uninstall"), "removed 0 links, restored 0 entries")
        for name in NAMES:
            link = provider_link(self.home, "grok", name)
            self.assertTrue(os.path.lexists(link))
            self.assertFalse(os.path.exists(link))
            self.assertEqual(os.readlink(link), texts[name])
        self.assertEqual(
            link_pairs(self.home),
            {(str(provider_link(self.home, "codex", name)), str(later)) for name in NAMES},
        )
        self.ok(run(self.home, later, "--harness", "codex", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_no_manifest()

    def test_row_11b_deleted_owner_is_restored_untracked_beside_a_live_owner(self):
        a, b = self.two()
        later = make_checkout(self.home, "l")
        self.ok(run(self.home, a, "--harness", "grok"))
        texts = {name: os.readlink(provider_link(self.home, "grok", name)) for name in NAMES}
        self.ok(run(self.home, later, "--harness", "codex"))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        shutil.rmtree(a)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        for name in NAMES:
            link = provider_link(self.home, "grok", name)
            self.assertTrue(os.path.lexists(link))
            self.assertFalse(os.path.exists(link))
            self.assertEqual(os.readlink(link), texts[name])
        self.assertEqual(
            link_pairs(self.home),
            {(str(provider_link(self.home, "codex", name)), str(later)) for name in NAMES},
        )
        self.ok(run(self.home, later, "--harness", "codex", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_no_manifest()

    def test_row_12a_dry_run_matches_a_covered_foreign_file(self):
        a, b = self.two()
        self.share_skills(a, b)
        swarm = provider_link(self.home, "grok", "swarm")
        swarm.parent.mkdir(parents=True)
        swarm.write_bytes(b"foreign\x00file\n")
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        before = snapshot(self.home)
        self.ok(
            run(self.home, a, "--harness", "grok", "uninstall", "--dry-run"),
            "would remove 0 links, would restore 0 entries",
        )
        self.assertEqual(snapshot(self.home), before)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assert_grok_text(b)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
        self.assertEqual(swarm.read_bytes(), b"foreign\x00file\n")
        self.assertFalse(swarm.is_symlink())
        self.assert_no_manifest()

    def test_row_12b_dry_run_counts_each_path_once(self):
        a, b = self.base()
        before = snapshot(self.home)
        self.ok(
            run(self.home, b, "--harness", "grok", "uninstall", "--dry-run"),
            "would remove 3 links, would restore 3 entries",
        )
        self.assertEqual(snapshot(self.home), before)
        self.assert_grok_text(b)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(a)
        self.assertEqual(link_pairs(self.home), grok_pairs(self.home, a))

    def test_row_13_three_replace_cycles_keep_one_row_per_path(self):
        a, b = self.two()
        self.ok(run(self.home, a, "--harness", "grok"))
        for _ in range(3):
            self.ok(run(self.home, b, "--harness", "grok", "--replace"))
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            data = read_v2(self.home)
            self.assertEqual(len(data["links"]), 3)
            self.assertEqual(len({row["path"] for row in data["links"]}), 3)
            self.assertEqual({row["checkout"] for row in data["links"]}, {str(a)})
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_gone()
        self.assert_no_manifest()

    def legacy_links(self, shape, paths):
        links = []
        for path in paths:
            if shape == "harnesses":
                row = {"harnesses": ["grok"], "path": path}
            elif shape == "harness":
                row = {"harness": "grok", "path": path}
            else:
                row = path
            links.append(row)
            links.append(row)
        return links

    def plant_legacy(self, shape):
        a = make_checkout(self.home, "a")
        self.ok(run(self.home, a, "--harness", "grok"))
        paths = [str(provider_link(self.home, "grok", name)) for name in NAMES]
        v2_file(self.home).unlink()
        legacy_file(self.home).write_text(json.dumps({"links": self.legacy_links(shape, paths), "backups": []}) + "\n")
        return a, paths

    def legacy_shape(self, shape):
        a, paths = self.plant_legacy(shape)
        self.ok(run(self.home, a, "--harness", "grok"), "linked 0 skills into nothing (already installed)")
        self.assertFalse(legacy_file(self.home).exists())
        data = read_v2(self.home)
        self.assertEqual(len(data["links"]), 3)
        self.assertEqual({(row["path"], row["checkout"]) for row in data["links"]}, {(path, str(a)) for path in paths})

        self.use_fresh()
        a, paths = self.plant_legacy(shape)
        b = make_checkout(self.home, "b")
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assertEqual(link_pairs(self.home), {(path, str(a)) for path in paths})
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_gone()
        self.assert_no_manifest()

        self.use_fresh()
        a, paths = self.plant_legacy(shape)
        b = make_checkout(self.home, "b")
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertEqual(link_pairs(self.home), {(path, str(a)) for path in paths})
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_gone()
        self.assert_no_manifest()

    def test_row_14_harnesses_shape(self):
        self.legacy_shape("harnesses")

    def test_row_14_harness_shape(self):
        self.legacy_shape("harness")

    def test_row_14_bare_string_shape(self):
        self.legacy_shape("bare")

    def test_row_14b_legacy_backup_restores_an_untracked_link(self):
        a, b = self.two()
        plant_links(self.home, b)
        backup_dir = state_dir(self.home) / "backups" / "old" / "grok"
        backup_dir.mkdir(parents=True)
        links = []
        backups = []
        for name in NAMES:
            original = str(provider_link(self.home, "grok", name))
            backup = backup_dir / name
            os.symlink(a / "skills" / name, backup)
            links.append({"harnesses": ["grok"], "path": original})
            links.append({"harnesses": ["grok"], "path": original})
            backups.append({"harnesses": ["grok"], "original": original, "backup": str(backup)})
        legacy_file(self.home).parent.mkdir(parents=True, exist_ok=True)
        legacy_file(self.home).write_text(json.dumps({"links": links, "backups": backups}) + "\n")
        sentinel = self.home / "sentinel.txt"
        sentinel.write_text("keep\n")
        skill_bytes = {
            checkout / "skills" / name / "SKILL.md": (checkout / "skills" / name / "SKILL.md").read_bytes()
            for checkout in (a, b)
            for name in NAMES
        }
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(a)
        self.assert_no_manifest()
        for path, content in skill_bytes.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(sentinel.read_text(), "keep\n")
        self.ok(
            run(self.home, a, "--harness", "grok"),
            "linked 0 skills into nothing (already installed)",
            "3 links already point at this checkout but are not tracked; uninstall leaves them",
        )
        self.assertFalse(v2_file(self.home).exists())
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assert_grok_text(a)
        self.assertEqual(sentinel.read_text(), "keep\n")

    def test_row_14c_old_manifest_file_cannot_erase_current_rows(self):
        a = make_checkout(self.home, "a")
        other = make_checkout(self.home, "c")
        self.ok(run(self.home, a, "--harness", "grok"))
        self.assertTrue(v2_file(self.home).exists())
        self.assertFalse(legacy_file(self.home).exists())
        before = v2_file(self.home).read_bytes()
        plant_links(self.home, other, "codex")
        legacy_file(self.home).write_text(json.dumps({
            "links": [{"harnesses": ["codex"], "path": str(provider_link(self.home, "codex", name))} for name in NAMES],
            "backups": [],
        }) + "\n")
        legacy_file(self.home).unlink()
        self.assertEqual(v2_file(self.home).read_bytes(), before)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_gone()
        self.assert_no_manifest()
        for name in NAMES:
            link = provider_link(self.home, "codex", name)
            self.assertEqual(os.readlink(link), str(other / "skills" / name))

    def test_project_scope_refuses_a_directory_shared_with_user_scope(self):
        a = make_checkout(self.home, "a")
        self.ok(run(self.home, a, "--harness", "grok"))
        before = v2_file(self.home).read_bytes()
        project = self.home / "project"
        project_skills = project / ".grok" / "skills"
        project_skills.parent.mkdir(parents=True)
        os.symlink(self.home / ".grok" / "skills", project_skills)
        refusal = f"grok: {project_skills} already resolves to {self.home / '.grok' / 'skills'}; nothing to link"
        self.ok(run(self.home, a, "--project", str(project), "--harness", "grok"), refusal)
        self.assertEqual(v2_file(self.home).read_bytes(), before)
        self.assert_grok_text(a)
        self.assertFalse((project / ".pstack" / "install-manifest-v2.json").exists())
        self.assertFalse((project / ".pstack" / "install-manifest.json").exists())

    def share_cursor_with_agents(self):
        agents = self.home / ".agents" / "skills"
        agents.mkdir(parents=True)
        (agents / "swarm").write_bytes(b"foreign\x00file\n")
        cursor = self.home / ".cursor" / "skills"
        cursor.parent.mkdir(parents=True)
        os.symlink(agents, cursor)
        return agents

    def test_c3_shared_provider_directory_carries_both_harnesses(self):
        a, b = self.two()
        agents = self.share_cursor_with_agents()
        self.ok(run(self.home, a, "--harness", "codex", "--replace"))
        self.ok(run(self.home, b, "--harness", "cursor", "--replace"))
        self.ok(
            run(self.home, a, "--harness", "codex", "uninstall"),
            "removed 0 links, restored 0 entries",
            "kept entries whose directory is shared with cursor; select those harnesses too to remove them",
        )
        self.assertEqual(os.readlink(agents / "swarm"), str(b / "skills" / "swarm"))
        self.ok(
            run(self.home, a, "--harness", "codex,cursor", "uninstall"),
            "removed 3 links, restored 0 entries",
            "3 of them had been moved aside by another checkout's --replace",
        )
        self.assertEqual(os.readlink(agents / "swarm"), str(b / "skills" / "swarm"))
        self.ok(
            run(self.home, b, "--harness", "codex,cursor", "uninstall"),
            "removed 3 links, restored 1 entries",
        )
        self.assertEqual((agents / "swarm").read_bytes(), b"foreign\x00file\n")
        self.assertFalse((agents / "swarm").is_symlink())
        self.assert_no_manifest()

    def test_partial_selection_does_not_restore_over_a_live_link(self):
        a = make_checkout(self.home, "a")
        agents = self.share_cursor_with_agents()
        self.ok(run(self.home, a, "--harness", "codex", "--replace"))
        self.ok(run(self.home, a, "--harness", "cursor"), "linked 0 skills into nothing (already installed)")
        text = os.readlink(agents / "swarm")
        self.assertEqual(text, str(a / "skills" / "swarm"))
        saved = [path.read_bytes() for path in (state_dir(self.home) / "backups").rglob("swarm") if path.is_file()]
        self.assertEqual(saved, [b"foreign\x00file\n"])
        before = snapshot(self.home)
        self.ok(
            run(self.home, a, "--harness", "codex", "uninstall"),
            "removed 0 links, restored 0 entries",
            "kept entries whose directory is shared with cursor; select those harnesses too to remove them",
        )
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(os.readlink(agents / "swarm"), text)
        saved_after = [path.read_bytes() for path in (state_dir(self.home) / "backups").rglob("swarm") if path.is_file()]
        self.assertEqual(saved_after, [b"foreign\x00file\n"])

    def test_crash_between_action_halves_converges(self):
        a = make_checkout(self.home, "a")
        foreign = provider_link(self.home, "grok", "alpha")
        foreign.parent.mkdir(parents=True)
        foreign.write_bytes(b"keep-me\n")
        swarm = provider_link(self.home, "grok", "swarm")
        write_v2(self.home, [{"path": str(swarm), "harnesses": ["grok"], "checkout": str(a)}], [])
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertFalse(os.path.lexists(swarm))
        self.assertEqual(foreign.read_bytes(), b"keep-me\n")
        self.assert_no_manifest()

        self.use_fresh()
        a = make_checkout(self.home, "a")
        plant_links(self.home, a)
        backups = []
        for name in NAMES:
            link = str(provider_link(self.home, "grok", name))
            backups.append({
                "original": link,
                "harnesses": ["grok"],
                "backup": str(state_dir(self.home) / "backups" / "missing" / name),
                "displaced": {"path": link, "harnesses": ["grok"], "checkout": str(a)},
            })
        write_v2(self.home, [], backups)
        kept = self.home / "kept.txt"
        kept.write_text("kept\n")
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_gone()
        self.assertEqual(kept.read_text(), "kept\n")
        self.assert_no_manifest()

        self.use_fresh()
        a = make_checkout(self.home, "a")
        plant_links(self.home, a)
        swarm = provider_link(self.home, "grok", "swarm")
        swarm.unlink()
        swarm.write_bytes(b"foreign\x00file\n")
        links = [
            {"path": str(provider_link(self.home, "grok", name)), "harnesses": ["grok"], "checkout": str(a)}
            for name in NAMES
        ]
        write_v2(self.home, links, [])
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 2 links, restored 0 entries")
        self.assertEqual(swarm.read_bytes(), b"foreign\x00file\n")
        self.assertFalse(swarm.is_symlink())
        for name in ("alpha", "pstack-runtime"):
            self.assertFalse(os.path.lexists(provider_link(self.home, "grok", name)))
        self.assert_no_manifest()

        self.use_fresh()
        a, b = self.two()
        plant_links(self.home, b)
        links = [
            {"path": str(provider_link(self.home, "grok", name)), "harnesses": ["grok"], "checkout": str(b)}
            for name in NAMES
        ]
        backups = []
        for name in NAMES:
            link = str(provider_link(self.home, "grok", name))
            backups.append({
                "original": link,
                "harnesses": ["grok"],
                "backup": str(state_dir(self.home) / "backups" / "gone" / name),
                "displaced": {"path": link, "harnesses": ["grok"], "checkout": str(a)},
            })
        write_v2(self.home, links, backups)
        kept = self.home / "kept.txt"
        kept.write_text("kept\n")
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assert_grok_text(b)
        self.assertEqual(link_pairs(self.home), grok_pairs(self.home, b))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_gone()
        self.assertEqual(kept.read_text(), "kept\n")
        self.assert_no_manifest()

        self.use_fresh()
        a = make_checkout(self.home, "a")
        swarm = provider_link(self.home, "grok", "swarm")
        swarm.parent.mkdir(parents=True)
        swarm.write_bytes(b"foreign\x00file\n")
        write_v2(self.home, [], [{
            "original": str(swarm),
            "harnesses": ["grok"],
            "backup": str(state_dir(self.home) / "backups" / "gone" / "swarm"),
            "displaced": None,
        }])
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertEqual(swarm.read_bytes(), b"foreign\x00file\n")
        self.assert_no_manifest()

        self.use_fresh()
        a = make_checkout(self.home, "a")
        plant_links(self.home, a)
        backups = []
        for name in NAMES:
            link = str(provider_link(self.home, "grok", name))
            backups.append({
                "original": link,
                "harnesses": ["grok"],
                "backup": str(state_dir(self.home) / "backups" / "gone" / name),
                "displaced": {"path": link, "harnesses": ["grok"], "checkout": str(a)},
            })
        write_v2(self.home, [], backups)
        kept = self.home / "kept.txt"
        kept.write_text("kept\n")
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_gone()
        self.assertEqual(kept.read_text(), "kept\n")
        self.assert_no_manifest()
