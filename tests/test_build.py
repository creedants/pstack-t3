import hashlib
import json
import os
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
                    self.assertEqual(built_mode, source_mode, output_rel)
                    if expected is not None:
                        self.assertEqual(source_mode, expected, source_rel)
                        self.assertEqual(built_mode, expected, output_rel)

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
