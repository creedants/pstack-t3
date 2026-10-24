if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import re
import unittest
from pathlib import Path
from .support import setUpModule, ReleaseCase, commit, run, repository, release
_TICKS = re.compile(r"`+")
_LINK = re.compile(r"!?\[([^\]\n]*)\]\(([^)\s]+)(?:[^)]*)\)")
def _open_fence(line):
  stripped = line.lstrip()
  if len(stripped) < 3 or stripped[0] not in "`~":
    return None
  run = len(stripped) - len(stripped.lstrip(stripped[0]))
  if run < 3:
    return None
  return (stripped[0], run)
def _shut_fence(fence, line):
  stripped = line.lstrip()
  if not stripped.startswith(fence[0]):
    return False
  run = len(stripped) - len(stripped.lstrip(fence[0]))
  return run >= fence[1] and not stripped[run:].strip()
def _spans(line):
  runs = [match.span() for match in _TICKS.finditer(line)]
  out = []
  pos = 0
  while pos < len(runs):
    width = runs[pos][1] - runs[pos][0]
    mate = None
    for cand in range(pos + 1, len(runs)):
      if runs[cand][1] - runs[cand][0] == width:
        mate = cand
        break
    if mate is None:
      pos += 1
    else:
      out.append((runs[pos][0], runs[mate][1]))
      pos = mate + 1
  return out
def _covered(spans, at):
  return any(start <= at < end for start, end in spans)
def active_relative_targets(text):
  targets = []
  fence = None
  for raw in text.splitlines():
    if fence is None:
      opened = _open_fence(raw)
      if opened is not None:
        fence = opened
        continue
    else:
      if _shut_fence(fence, raw):
        fence = None
      continue
    zones = _spans(raw)
    for m in _LINK.finditer(raw):
      dest = m.group(2)
      if not dest:
        continue
      if ":" in dest:
        continue
      if dest[0] in "/#<":
        continue
      if dest.startswith("../../"):
        targets.append(dest)
        continue
      opener = m.start() + (m.group(0)[0] == "!")
      if _covered(zones, opener) or _covered(zones, m.start(2)):
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
