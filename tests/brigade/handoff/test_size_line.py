if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import Path, SCRIPT, run_globals
from .fixtures import size_warning


class SizeLineMixin:
    def test_a_handoff_over_40000_bytes_warns_and_keeps_the_watch_text(self):
        self.open()
        self.assert_watch()
        (self.at / "handoff.md").write_bytes(b"x" * 40001)
        self.assert_watch(size_warning(40001))

    def test_a_handoff_of_40000_bytes_prints_no_size_line(self):
        self.open()
        (self.at / "handoff.md").write_bytes(b"x" * 40000)
        self.assert_watch()

    def test_a_missing_handoff_prints_no_size_line(self):
        self.open()
        self.assertFalse((self.at / "handoff.md").exists())
        self.assert_watch()

    def test_a_directory_named_handoff_md_prints_no_size_line(self):
        self.open()
        (self.at / "handoff.md").mkdir()
        self.assert_watch()

    def test_a_symlink_to_a_large_handoff_warns_with_the_target_size(self):
        self.open()
        target = Path(self.temporary.name) / "archive.md"
        target.write_bytes(b"x" * 40001)
        (self.at / "handoff.md").symlink_to(target)
        self.assert_watch(size_warning(40001))

    def test_a_stat_error_on_handoff_md_returns_no_size_line(self):
        self.open()
        line = run_globals(self, SCRIPT)["watch"].__globals__["handoff_size_line"]
        real_is_file = Path.is_file
        real_stat = Path.stat

        def is_file(self):
            if self.name == "handoff.md":
                return True
            return real_is_file(self)

        def stat(self, *, follow_symlinks=True):
            if self.name == "handoff.md":
                raise OSError("stat failed")
            return real_stat(self, follow_symlinks=follow_symlinks)

        Path.is_file = is_file
        Path.stat = stat
        try:
            self.assertIsNone(line(self.at))
            self.assert_watch(in_process=True)
        finally:
            Path.is_file = real_is_file
            Path.stat = real_stat
