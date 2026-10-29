"""scripts/sync_tstack.py against a modeled GitHub, with no network."""

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import sync_tstack


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


OLD = "1" * 40
NEW = "2" * 40
SKILLS = ["skills/full-send/SKILL.md", "skills/sol-loop/SKILL.md"]


def link(commit, skill="full-send"):
    return f"https://github.com/t3dotgg/tstack/blob/{commit}/skills/{skill}/SKILL.md"


def github(files):
    """A gh_api stand-in. main resolves to NEW, and files maps (commit, path) to text. A missing file answers None, as a 404 does."""
    def gh(path):
        if path == "repos/t3dotgg/tstack/commits/main":
            return {"sha": NEW}
        name, _, ref = path.removeprefix("repos/t3dotgg/tstack/contents/").partition("?ref=")
        text = files.get((ref, name))
        return None if text is None else {"content": base64.b64encode(text.encode()).decode()}
    return gh


SAME = {(commit, path): f"{path}\nsteps\n" for commit in (OLD, NEW) for path in SKILLS}
CHANGED = SAME | {(NEW, SKILLS[0]): f"{SKILLS[0]}\nnew steps\n"}


class SyncTstackTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        pin = {"repository": "https://github.com/t3dotgg/tstack", "commit": OLD, "skills": SKILLS,
               "license": "MIT", "author": "t3dotgg"}
        self.write("tstack.json", json.dumps(pin, indent=2) + "\n")
        self.write("t3/added/brigade/full_send.py", f'LINKS = ["{link(OLD)}", "{link(OLD, "sol-loop")}"]\n')
        self.write("docs/full-send.md", f"[full-send]({link(OLD)})\n")
        self.write("skills/brigade/full_send.py", f'LINKS = ["{link(OLD)}"]\n')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def files(self):
        return {path.relative_to(self.root).as_posix(): path.read_text() for path in sorted(self.root.rglob("*")) if path.is_file()}

    def run_sync(self, files, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sync_tstack.main(list(argv), root=self.root, gh=github(files))
        return code, out.getvalue(), err.getvalue()

    def test_a_plain_run_prints_each_skill_diff_and_writes_nothing(self):
        before = self.files()
        code, out, err = self.run_sync(CHANGED)
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"-steps\n+new steps\n", out)
        self.assertIn(f"{SKILLS[1]}: unchanged\n", out)
        self.assertEqual(self.files(), before)

    def test_bump_moves_the_pin_and_every_source_link_that_named_it(self):
        """The defect: a bump moves tstack.json but leaves a source link at the old commit, or edits generated skills/."""
        generated = self.files()["skills/brigade/full_send.py"]
        code, _, err = self.run_sync(CHANGED, "--bump")
        self.assertEqual((code, err), (0, ""))
        after = self.files()
        self.assertEqual(json.loads(after["tstack.json"])["commit"], NEW)
        self.assertEqual(after["t3/added/brigade/full_send.py"], f'LINKS = ["{link(NEW)}", "{link(NEW, "sol-loop")}"]\n')
        self.assertEqual(after["docs/full-send.md"], f"[full-send]({link(NEW)})\n")
        self.assertEqual(after["skills/brigade/full_send.py"], generated)

    def test_bump_refuses_and_writes_nothing_when_a_pinned_skill_is_missing_at_the_ref(self):
        """The defect: a bump pins a commit where a linked skill no longer exists."""
        before = self.files()
        missing = {key: text for key, text in SAME.items() if key != (NEW, SKILLS[1])}
        code, out, err = self.run_sync(missing, "--bump")
        self.assertEqual(code, 1)
        self.assertIn(f"{SKILLS[1]}: missing at {NEW}\n", out)
        self.assertEqual(err, f"sync_tstack: not bumped; {SKILLS[1]} missing at {NEW}\n")
        self.assertEqual(self.files(), before)


if __name__ == "__main__":
    unittest.main()
