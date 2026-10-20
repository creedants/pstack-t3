import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build  # noqa: E402
import check  # noqa: E402

CASES = (
    ("t3/added/brigade/scripts/brigade.py", "brigade/scripts/brigade.py", 0o644),
    ("t3/added/brigade/scripts/activity.py", "brigade/scripts/activity.py", 0o644),
    ("t3/added/brigade-admin/SKILL.md", "brigade-admin/SKILL.md", None),
    ("t3/added/landing/scripts/land.py", "landing/scripts/land.py", 0o644),
    (
        "vendor/pstack/skills/poteto-mode/scripts/watch-pr/watch-pr",
        "poteto-mode/scripts/watch-pr/watch-pr",
        0o755,
    ),
    ("t3/scripts/roles.py", "pstack-runtime/scripts/roles.py", None),
    # Covers the 12 split files today.
    *(
        (
            f"t3/scripts/_roles/{p.name}",
            f"pstack-runtime/scripts/_roles/{p.name}",
            None,
        )
        for p in sorted((ROOT / "t3/scripts/_roles").glob("*.py"))
    ),
    (
        "t3/overrides/poteto-mode/scripts/check-plan.mjs",
        "poteto-mode/scripts/check-plan.mjs",
        None,
    ),
    (
        "t3/overrides/poteto-mode/scripts/worktree-audit.sh",
        "poteto-mode/scripts/worktree-audit.sh",
        None,
    ),
    (
        "vendor/pstack/skills/poteto-mode/scripts/orch/orch.ts",
        "poteto-mode/scripts/orch/orch.ts",
        None,
    ),
    ("t3/runtime.md", "pstack-runtime/SKILL.md", None),
)


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


class BuildModeTest(unittest.TestCase):
    def test_built_files_keep_source_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "skills"
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/build.py"), "--out", str(out)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for source_rel, output_rel, expected in CASES:
                with self.subTest(path=output_rel):
                    source = ROOT / source_rel
                    built = out / output_rel
                    self.assertTrue(built.is_file(), output_rel)
                    source_mode = stat.S_IMODE(source.stat().st_mode)
                    built_mode = stat.S_IMODE(built.stat().st_mode)
                    self.assertEqual(built_mode, source_mode | stat.S_IWUSR, output_rel)
                    if expected is not None:
                        self.assertEqual(source_mode | stat.S_IWUSR, expected, source_rel)
                        self.assertEqual(built_mode, expected, output_rel)

    def test_a_read_only_copy_builds_the_same_files_and_modes_as_a_writable_one(self):
        # The override layer copies architect/SKILL.md over the file the vendor layer wrote.
        for rel in ("vendor/pstack/skills/architect/SKILL.md", "t3/overrides/architect/SKILL.md"):
            self.assertTrue((ROOT / rel).is_file(), rel)

        def set_owner_write(root, writable):
            for path in root.rglob("*"):
                if path.is_file():
                    mode = stat.S_IMODE(path.stat().st_mode)
                    path.chmod(mode | stat.S_IWUSR if writable else mode & ~0o222)

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            copy = base / "copy"
            for rel in tree_files(ROOT):
                if (ROOT / rel).is_file():
                    (copy / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / rel, copy / rel)
            built = {}
            for label, writable in (("writable", True), ("read-only", False)):
                set_owner_write(copy, writable)
                out = base / label / "skills"
                result = subprocess.run(
                    [sys.executable, str(copy / "scripts/build.py"), "--out", str(out)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, f"{label}: {result.stdout}{result.stderr}")
                built[label] = {
                    path.relative_to(out).as_posix(): (
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                        stat.S_IMODE(path.stat().st_mode),
                    )
                    for path in out.rglob("*")
                    if path.is_file()
                }
            self.assertEqual(built["read-only"], built["writable"])

    def test_build_fails_when_the_repository_badge_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "skills"
            stale = ["README.md badge url 0.0.0 alt 0.0.0 disagrees with upstream.json version 1.1.0"]
            with mock.patch.object(check, "badge_findings", return_value=stale):
                with self.assertRaises(SystemExit) as raised:
                    build.build(out)
            self.assertIn("check failed", str(raised.exception))
            self.assertIn(stale[0], str(raised.exception))


class OverrideLockTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock = self.root / "t3/overrides.lock.json"
        for name, value in (("T3", self.root / "t3"), ("VENDOR", self.root / "vendor/pstack"), ("LOCK", self.lock)):
            patcher = mock.patch.object(build, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.root / "t3/overrides/alpha").mkdir(parents=True)
        (self.root / "t3/agents").mkdir(parents=True)

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def write_lock(self, digests):
        self.lock.write_text(json.dumps(digests, indent=2, sort_keys=True) + "\n")

    def lock_line(self, rel, key):
        self.write_lock({key: hashlib.sha256((self.root / f"vendor/pstack/{rel}").read_bytes()).hexdigest()})

    def test_a_changed_vendored_persona_reports_drift_and_names_the_t3_file(self):
        self.write("t3/agents/persona.md", "t3 persona\n")
        self.write("vendor/pstack/agents/persona.md", "upstream persona\n")
        self.lock_line("agents/persona.md", "agents/persona.md")
        self.write("vendor/pstack/agents/persona.md", "upstream persona changed\n")
        self.assertEqual(
            build.check_lock(False),
            [
                "t3/agents/persona.md: upstream changed since this persona was written; "
                "review or re-port it, then --update-lock"
            ],
        )

    def test_a_changed_vendored_override_keeps_its_skill_path_in_the_message(self):
        self.write("t3/overrides/alpha/SKILL.md", "t3 alpha\n")
        self.write("vendor/pstack/skills/alpha/SKILL.md", "upstream alpha\n")
        self.lock_line("skills/alpha/SKILL.md", "alpha/SKILL.md")
        self.write("vendor/pstack/skills/alpha/SKILL.md", "upstream alpha changed\n")
        self.assertEqual(
            build.check_lock(False),
            [
                "alpha/SKILL.md: upstream changed since this override was written; "
                "review or re-port it, then --update-lock"
            ],
        )

    def test_a_skill_override_with_no_vendored_counterpart_reports_a_missing_lock_key_and_writes_null(self):
        self.write("t3/overrides/alpha/SKILL.md", "t3 alpha\n")
        self.write_lock({})
        self.assertEqual(
            build.check_lock(False),
            ["alpha/SKILL.md: override not in overrides.lock.json (run build.py --update-lock after reviewing)"],
        )
        build.check_lock(True)
        self.assertEqual(json.loads(self.lock.read_text()), {"alpha/SKILL.md": None})

    def test_a_null_lock_key_for_a_skill_override_with_no_vendored_counterpart_is_not_drift(self):
        self.write("t3/overrides/alpha/SKILL.md", "t3 alpha\n")
        self.write_lock({"alpha/SKILL.md": None})
        self.assertEqual(build.check_lock(False), [])
        build.check_lock(True)
        self.assertEqual(json.loads(self.lock.read_text()), {"alpha/SKILL.md": None})

    def test_a_skill_override_with_an_old_digest_reports_drift_and_refreshes_to_null(self):
        self.write("t3/overrides/alpha/SKILL.md", "t3 alpha\n")
        self.write_lock({"alpha/SKILL.md": hashlib.sha256(b"an older upstream file\n").hexdigest()})
        self.assertEqual(
            build.check_lock(False),
            [
                "alpha/SKILL.md: upstream changed since this override was written; "
                "review or re-port it, then --update-lock"
            ],
        )
        build.check_lock(True)
        self.assertEqual(json.loads(self.lock.read_text()), {"alpha/SKILL.md": None})

    def test_a_persona_with_no_vendored_file_is_not_drift_and_gets_no_key(self):
        self.write("t3/agents/persona.md", "t3 persona\n")
        self.assertEqual(build.check_lock(False), [])
        build.check_lock(True)
        self.assertEqual(json.loads(self.lock.read_text()), {})

    def test_a_vendored_persona_that_appears_fails_until_the_lock_is_refreshed(self):
        self.write("t3/agents/persona.md", "t3 persona\n")
        self.assertEqual(build.check_lock(False), [])
        self.write("vendor/pstack/agents/persona.md", "upstream persona\n")
        self.assertEqual(
            build.check_lock(False),
            ["t3/agents/persona.md: persona not in overrides.lock.json (run build.py --update-lock after reviewing)"],
        )
        build.check_lock(True)
        self.assertEqual(build.check_lock(False), [])

    def test_a_locked_persona_whose_vendored_file_is_gone_keeps_the_upstream_changed_sentence(self):
        self.write("t3/agents/persona.md", "t3 persona\n")
        self.write("vendor/pstack/agents/persona.md", "upstream persona\n")
        self.lock_line("agents/persona.md", "agents/persona.md")
        (self.root / "vendor/pstack/agents/persona.md").unlink()
        self.assertEqual(
            build.check_lock(False),
            [
                "t3/agents/persona.md: upstream changed since this persona was written; "
                "review or re-port it, then --update-lock"
            ],
        )

    def test_a_locked_key_with_no_t3_file_keeps_the_locked_but_no_persona_or_override_sentence(self):
        self.write("vendor/pstack/agents/persona.md", "upstream persona\n")
        self.write_lock(
            {
                "agents/persona.md": hashlib.sha256(b"upstream persona\n").hexdigest(),
                "gone/SKILL.md": hashlib.sha256(b"nothing vendored\n").hexdigest(),
            }
        )
        self.assertEqual(
            build.check_lock(False),
            [
                "agents/persona.md: locked but no persona exists (run --update-lock)",
                "gone/SKILL.md: locked but no override exists (run --update-lock)",
            ],
        )

    def test_a_persona_under_t3_overrides_is_rejected_by_the_replacement_list(self):
        self.write("t3/overrides/agents/persona.md", "t3 persona\n")
        self.write("vendor/pstack/agents/persona.md", "upstream persona\n")
        with self.assertRaises(SystemExit) as raised:
            build.replacements()
        self.assertIn("t3/overrides/agents/persona.md: a persona belongs in t3/agents", str(raised.exception))

    def test_replacements_mints_the_persona_keys(self):
        self.write("t3/overrides/alpha/SKILL.md", "t3 alpha\n")
        self.write("vendor/pstack/skills/alpha/SKILL.md", "upstream alpha\n")
        self.write("t3/agents/persona.md", "t3 persona\n")
        self.write("t3/agents/other.md", "t3 other\n")
        self.assertEqual(
            [
                (row.lock_key, row.t3_path, row.upstream_rel, row.conflict_rel, row.pins)
                for row in build.replacements()
            ],
            [
                ("alpha/SKILL.md", "t3/overrides/alpha/SKILL.md", "skills/alpha/SKILL.md", "alpha/SKILL.md", True),
                ("agents/other.md", "t3/agents/other.md", "agents/other.md", "agents/other.md", False),
                ("agents/persona.md", "t3/agents/persona.md", "agents/persona.md", "agents/persona.md", False),
            ],
        )


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
    def test_default_build_writes_the_committed_command_line_reference_pages(self):
        listed = tree_files(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory)
            for rel in listed:
                if rel and not rel.startswith("docs/cli/") and (ROOT / rel).is_file():
                    (copy / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / rel, copy / rel)
            result = subprocess.run(
                [sys.executable, str(copy / "scripts/build.py")],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            built = sorted(path.name for path in (copy / "docs/cli").iterdir())
            self.assertEqual(built, sorted(path.name for path in (ROOT / "docs/cli").iterdir()))
            for name in built:
                with self.subTest(name):
                    self.assertEqual((copy / "docs/cli" / name).read_text(), (ROOT / "docs/cli" / name).read_text())
