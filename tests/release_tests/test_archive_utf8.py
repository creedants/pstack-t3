if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import unittest

from .support import (
    setUpModule,
    ReleaseCase,
    commit,
    repository,
)


class ArchiveUtf8Test(ReleaseCase):
    def test_refuses_an_archive_file_that_is_not_utf8(self):
        with repository() as repo:
            commit(
                repo,
                {"docs/changelog/0.1.0.md": b"## 0.1.0\n\n- caf\xe9\n", "changes/a.md": "- a\n"},
                day=2,
            )
            self.assert_refuses(repo, "0.3.0", "docs/changelog/0.1.0.md is not UTF-8; fix it\n")


if __name__ == "__main__":
    unittest.main()
