import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCH_PR = Path("poteto-mode/scripts/watch-pr/watch-pr")
FORCED_EXEC = {".sh", ".py", ".mjs"}


def executable(path):
    return bool(path.stat().st_mode & stat.S_IXUSR)


def source_map():
    """Last writer for each generated path, in the same order as scripts/build.py render."""
    found = {}
    removed = set()
    removed_file = ROOT / "t3/removed.txt"
    if removed_file.exists():
        for line in removed_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                removed.add(Path(line))
    vendor = ROOT / "vendor/pstack/skills"
    for path in vendor.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(vendor)
        if rel in removed or any(parent in removed for parent in rel.parents):
            continue
        found[rel] = path
    for layer in ("overrides", "added"):
        base = ROOT / "t3" / layer
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                found[path.relative_to(base)] = path
    found[Path("pstack-runtime/SKILL.md")] = ROOT / "t3/runtime.md"
    found[Path("pstack-runtime/scripts/roles.py")] = ROOT / "t3/scripts/roles.py"
    for persona in (ROOT / "t3/agents").glob("*.md"):
        found[Path("pstack-runtime/agents") / persona.name] = persona
    found[Path("setup-pstack/SKILL.md")] = ROOT / "t3/setup.md"
    return found


class BuildModeTest(unittest.TestCase):
    def test_every_built_file_keeps_its_source_executable_bit(self):
        sources = source_map()
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "skills"
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/build.py"), "--out", str(out)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            built = sorted(
                path.relative_to(out)
                for path in out.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            )
            self.assertEqual(built, sorted(sources))
            for rel in built:
                source = sources[rel]
                built_bit = executable(out / rel)
                source_bit = executable(source)
                forced = rel.suffix in FORCED_EXEC or rel.name == "orch.ts"
                if source_bit or not forced:
                    self.assertEqual(built_bit, source_bit, str(rel))
                else:
                    self.assertTrue(built_bit, str(rel))
            vendor_watch = ROOT / "vendor/pstack/skills" / WATCH_PR
            built_watch = out / WATCH_PR
            self.assertTrue(executable(vendor_watch))
            self.assertTrue(executable(built_watch))
            self.assertEqual(executable(built_watch), executable(vendor_watch))
