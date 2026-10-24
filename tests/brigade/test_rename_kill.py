if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import BrigadeTest, json, os, setUpModule


class RenameKillTest(BrigadeTest):
    """A rename rerun completes after a kill between the marker claim and its first write."""

    def test_a_rerun_adopts_an_empty_marker_left_by_a_killed_claim(self):
        """A rerun adopts an empty marker when the target is missing and moves the store."""
        self.open()
        self.brigade("set", "--thread", "coordinator")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        meta = json.loads((self.at / "restaurant.json").read_text())
        tables = self.tables()
        rail = (self.at / "rail.tsv").read_bytes()
        marker = self.at.parent / ".rename-speed.json"
        os.close(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
        self.assertTrue(marker.is_file())
        self.assertEqual(marker.stat().st_size, 0)
        self.assertFalse((self.at.parent / "speed").exists())
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--rename", "Speed")
        speed = self.at.parent / "speed"
        self.assertFalse(self.at.exists())
        self.assertFalse(marker.exists())
        moved = json.loads((speed / "restaurant.json").read_text())
        self.assertEqual(moved["restaurant"], "Speed")
        self.assertEqual(moved["thread"], meta["thread"])
        self.assertEqual(moved["generation"], meta["generation"])
        self.assertEqual((speed / "rail.tsv").read_bytes(), rail)
        self.assertIn(b"T1\t", rail)
        self.assertEqual({path.name: path.read_bytes() for path in sorted(speed.glob("*.tsv"))}, tables)
