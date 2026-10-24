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

ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"\]\(([^)]+)\)")


class OnDemandRuntimeDocTest(unittest.TestCase):
    def test_on_demand_table_lists_every_runtime_doc_so_no_file_is_unreachable(self):
        text = (ROOT / "skills/pstack-runtime/SKILL.md").read_text()
        table = text.split("## On-demand files", 1)[1].split("\n## ", 1)[0]
        linked = {
            Path(match.group(1)).name
            for line in table.splitlines()
            if line.startswith("| [")
            for match in [LINK.search(line)]
            if match
        }
        docs = {path.name for path in (ROOT / "skills/pstack-runtime").glob("*.md")} - {"SKILL.md"}
        self.assertEqual(docs - linked, set())


class MakeBotUiRouteTest(unittest.TestCase):
    def test_make_bot_ui_names_the_external_key_file_and_nonempty_check(self):
        text = (ROOT / "skills/make-bot-ui/SKILL.md").read_text()
        for phrase in ("<ui-dir>/.webhook-key", "test -s"):
            self.assertIn(phrase, text)
        self.assertIn("## Get the webhook routine", text)
