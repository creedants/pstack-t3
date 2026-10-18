import unittest
from pathlib import Path
from .support import setUpModule, ReleaseCase, commit, run, repository, release
def active_relative_targets(text):
  targets = []
  in_fence = False
  for raw in text.splitlines():
    stripped = raw.lstrip()
    if stripped.startswith("```") or stripped.startswith("~~~"):
      in_fence = not in_fence
      continue
    if in_fence:
      continue
    segs = release.CODERE.split(raw)
    for i in range(0, len(segs), 2):
      for m in release.LINK_RE.finditer(segs[i]):
        dest = m.group(2)
        if not dest:
          continue
        if "://" in dest:
          continue
        if ":" in dest:
          continue
        if dest[0] in "/#<":
          continue
        if dest.startswith("../../"):
          targets.append(dest)
          continue
        targets.append(dest)
  return targets
class ArchiveLinksTest(ReleaseCase):
  def test_every_active_relative_link_resolves(self):
    root = Path(__file__).resolve().parents[2]
    base = root / "docs/changelog"
    self.assertTrue(base.is_dir())
    missing = []
    for path in sorted(base.glob("*.md")):
      for dest in active_relative_targets(path.read_text()):
        cleaned = dest.split("#", 1)[0]
        if not cleaned:
          continue
        if not (base / cleaned).exists():
          missing.append(f"{path.name}: {dest}")
    self.assertEqual(missing, [])
  def test_rollover_rebases_a_link(self):
    bullets = "".join(f"- bullet {i} {'x' * 80}\n" for i in range(300))
    old = "# Changelog\n\n## 0.1.0 (2026-10-06)\n\n- [guide](docs/guide.md)\n" + bullets
    with repository(changelog=old) as repo:
      commit(repo, {"changes/a.md": "- [other](docs/other.md)\n- a\n"}, day=2)
      self.assertEqual(run(repo, "0.1.1").returncode, 0)
      changelog_text = (repo / "CHANGELOG.md").read_text()
      self.assertIn("- [other](docs/other.md)", changelog_text)
      self.assertNotIn("../../docs/other.md", changelog_text)
      self.assertTrue(release.fits(changelog_text))
      archived = sorted((repo / "docs/changelog").glob("*.md"))
      self.assertTrue(archived)
      joined = ""
      for p in archived:
        txt = p.read_text()
        self.assertTrue(release.fits(txt))
        joined += txt
      self.assertIn("[guide](../../docs/guide.md)", joined)
      self.assertNotIn("[guide](docs/guide.md)", joined)
if __name__ == "__main__":
  unittest.main()
