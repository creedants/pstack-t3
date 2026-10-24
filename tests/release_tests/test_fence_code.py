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
from .support import setUpModule, ReleaseCase, commit, run, repository, release
def padding():
  return "".join(f"- bullet {i} {'x' * 80}\n" for i in range(300))
def archived_join(repo):
  parts = sorted((repo / "docs/changelog").glob("*.md"))
  assert parts
  joined = ""
  for path in parts:
    text = path.read_text()
    assert release.fits(text)
    joined += text
  return joined
class FenceCodeTest(ReleaseCase):
  def test_shorter_fence_inside_longer_fence_keeps_code_bytes(self):
    block = "- example\n  ````md\n  ```\n  [example](docs/guide.md)\n  ````\n"
    old = "# Changelog\n\n## 0.1.0 (2026-10-06)\n\n" + block + padding()
    with repository(changelog=old) as repo:
      commit(repo, {"changes/a.md": "- a\n"}, day=2)
      self.assertEqual(run(repo, "0.1.1").returncode, 0)
      joined = archived_join(repo)
      self.assertIn(block, joined)
      self.assertNotIn("[example](../../docs/guide.md)", joined)
  def test_double_backtick_span_holding_link_keeps_code_bytes(self):
    line = "- ``code with ` [guide](docs/guide.md) ` inside``\n"
    old = "# Changelog\n\n## 0.1.0 (2026-10-06)\n\n" + line + padding()
    with repository(changelog=old) as repo:
      commit(repo, {"changes/a.md": "- a\n"}, day=2)
      self.assertEqual(run(repo, "0.1.1").returncode, 0)
      joined = archived_join(repo)
      self.assertIn(line, joined)
      self.assertNotIn("[guide](../../docs/guide.md)", joined)
  def test_label_holding_code_span_rebases_its_destination(self):
    line = "- [`guide`](docs/guide.md)\n"
    old = "# Changelog\n\n## 0.1.0 (2026-10-06)\n\n" + line + padding()
    with repository(changelog=old) as repo:
      commit(repo, {"changes/a.md": "- a\n"}, day=2)
      self.assertEqual(run(repo, "0.1.1").returncode, 0)
      joined = archived_join(repo)
      self.assertIn("- [`guide`](../../docs/guide.md)\n", joined)
      self.assertNotIn("- [`guide`](docs/guide.md)\n", joined)
if __name__ == "__main__":
  unittest.main()
