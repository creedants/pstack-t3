if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check  # noqa: E402


def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull


def setUpModule():
    fence_git_environment()


def tree_files(root):
    """List files under root. Use git when git owns root as a work tree directory, and walk the tree otherwise.

    git owns root when ``git rev-parse --show-toplevel`` resolves to root, or to an ancestor of root
    that tracks a file under root. A git archive copy has no tracked files under its root, so the
    walk supplies its file list. The walk drops a .git entry, a __pycache__ directory, and a .pyc file.
    """
    root = Path(root)
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except OSError:
        top = None
    owned = False
    if top is not None and top.returncode == 0 and top.stdout:
        toplevel = Path(top.stdout.rstrip("\n")).resolve()
        if toplevel == root.resolve():
            owned = True
        elif toplevel in root.resolve().parents:
            cached = subprocess.run(
                ["git", "ls-files", "-z", "--cached"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            owned = cached.returncode == 0 and any(rel for rel in cached.stdout.split("\0"))
    if owned:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return [rel for rel in listed.stdout.split("\0") if rel]
    found = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in {".git", "__pycache__"}]
        for name in filenames:
            path = Path(directory) / name
            if name == ".git" or name.endswith(".pyc") or not path.is_file():
                continue
            found.append(path.relative_to(root).as_posix())
    return found


class TreeFilesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)

    def test_tree_files_excludes_an_ignored_file_under_a_tracked_subdirectory_root(self):
        sub = self.repo / "sub"
        sub.mkdir()
        (sub / ".gitignore").write_text("ignored.txt\n")
        (sub / "tracked.txt").write_text("tracked\n")
        (sub / "ignored.txt").write_text("ignored\n")
        subprocess.run(["git", "add", "sub/.gitignore", "sub/tracked.txt"], cwd=self.repo, check=True)
        listed = tree_files(sub)
        self.assertIn("tracked.txt", listed)
        self.assertNotIn("ignored.txt", listed)

    def test_tree_files_walks_an_archive_copy_nested_beneath_an_unrelated_repository(self):
        (self.repo / ".gitignore").write_text("*.log\n")
        (self.repo / "tracked.txt").write_text("tracked\n")
        subprocess.run(["git", "add", "tracked.txt", ".gitignore"], cwd=self.repo, check=True)
        copy = self.repo / "copy"
        copy.mkdir()
        (copy / "data.txt").write_text("data\n")
        (copy / "kept.log").write_text("kept\n")
        listed = tree_files(copy)
        self.assertIn("data.txt", listed)
        self.assertIn("kept.log", listed)


class BuildReferenceTest(unittest.TestCase):
    def _copy_repo(self, skip=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        copy = Path(tmp.name)
        for rel in tree_files(ROOT):
            if rel and (not skip or not rel.startswith(skip)) and (ROOT / rel).is_file():
                (copy / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, copy / rel)
        return copy

    def _build(self, copy, *args):
        return subprocess.run([sys.executable, str(copy / "scripts/build.py"), *args], capture_output=True, text=True)

    def _probe(self, copy):
        probe = copy / "t3/added/probe-skill/SKILL.md"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("---\nname: probe-skill\ndescription: d\n---\n\n# P\n\nBody.\n")

    def test_default_build_writes_the_committed_command_line_reference_pages(self):
        copy = self._copy_repo("docs/cli/")
        res = self._build(copy)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        built = sorted(p.name for p in (copy / "docs/cli").iterdir())
        self.assertEqual(built, sorted(p.name for p in (ROOT / "docs/cli").iterdir()))
        for name in built:
            with self.subTest(name):
                self.assertEqual((copy / "docs/cli" / name).read_text(), (ROOT / "docs/cli" / name).read_text())

    def test_default_build_rewrites_stale_guide_and_readme_counts_from_shipped_skills(self):
        copy = self._copy_repo()
        n = sum(1 for _ in (copy / "skills").glob("*/SKILL.md"))
        self._probe(copy)
        res = self._build(copy)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        guide, readme = copy / "docs/guide.md", copy / "README.md"
        g_text, r_text = guide.read_text(), readme.read_text()
        self.assertIn(f"{n + 1}/{n + 1} pstack-t3", g_text)
        self.assertNotIn(f"{n}/{n} pstack-t3", g_text)
        self.assertIn(f"all {n + 1} skills", r_text)
        self.assertNotIn(f"all {n} skills", r_text)
        self.assertEqual((check.guide_count_findings(guide, copy / "skills"), check.readme_count_findings(readme, copy / "skills")), ([], []))

    def test_default_build_fails_before_replacing_skills_when_guide_lacks_count(self):
        copy = self._copy_repo()
        guide, readme = copy / "docs/guide.md", copy / "README.md"
        guide.write_text(re.sub(r"\d+/\d+ pstack-t3", "none", guide.read_text()))
        readme.write_text(re.sub(r"\ball \d+ skills\b", "all 60 skills", readme.read_text()))
        self._probe(copy)
        res = self._build(copy)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("quotes no doctor count", res.stdout + res.stderr)
        self.assertFalse((copy / "skills/probe-skill").exists())
        self.assertIn("all 60 skills", readme.read_text())

    def test_explicit_out_to_repo_skills_keeps_guide_check_and_leaves_docs_alone(self):
        copy = self._copy_repo()
        guide, readme = copy / "docs/guide.md", copy / "README.md"
        g_before, r_before = guide.read_bytes(), readme.read_bytes()
        self._probe(copy)
        res = self._build(copy, "--out", str(copy / "skills"))
        self.assertNotEqual(res.returncode, 0)
        out = res.stdout + res.stderr
        self.assertIn("quotes the doctor count", out)
        self.assertIn("states all", out)
        self.assertFalse((copy / "skills/probe-skill").exists())
        self.assertEqual((guide.read_bytes(), readme.read_bytes()), (g_before, r_before))

    def test_explicit_out_to_repo_skills_with_agreeing_counts_regenerates_docs_and_leaves_guide_and_readme_untouched(self):
        copy = self._copy_repo("docs/cli/")
        (copy / "docs/skills.md").write_text("stale\n")
        guide, readme = copy / "docs/guide.md", copy / "README.md"
        g_before, r_before = guide.read_bytes(), readme.read_bytes()
        stamp = 1_000_000
        for path in (guide, readme):
            os.utime(path, (stamp, stamp))
        res = self._build(copy, "--out", str(copy / "skills"))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual((copy / "docs/skills.md").read_text(), (ROOT / "docs/skills.md").read_text())
        built = sorted(p.name for p in (copy / "docs/cli").iterdir())
        self.assertEqual(built, sorted(p.name for p in (ROOT / "docs/cli").iterdir()))
        for name in built:
            with self.subTest(name):
                self.assertEqual((copy / "docs/cli" / name).read_text(), (ROOT / "docs/cli" / name).read_text())
        self.assertEqual((guide.read_bytes(), readme.read_bytes()), (g_before, r_before))
        self.assertEqual((guide.stat().st_mtime_ns, readme.stat().st_mtime_ns), (stamp * 10**9, stamp * 10**9))

