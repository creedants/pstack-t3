if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

import tests.test_activity as test_activity

Fixture = test_activity.Fixture
MOD = test_activity.MOD
COORDINATOR = test_activity.COORDINATOR
delegated = test_activity.delegated
worker = test_activity.worker


def fence_git_environment():
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


def double(thread):
    return thread.replace("%2F", "%252F")


class PinnedActivityDefectsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fixture = Fixture(directory.name)
        self.addCleanup(self.fixture.close)

    def test_reviewers_with_double_encoded_slash_group_under_item_with_record_winning(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", state="merged", thread=worker(1))
        fixture.units[-1]["branch"] = "pstack-t3/d7"
        fixture.unit("D8", state="merged")
        fixture.units[-1]["branch"] = "pstack-t3/other"
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        request = "brigade-pstack-t3-pstack-t3/pstack-t3/d7-review-1"
        review = double(delegated(COORDINATOR, request))
        fixture.thread(review, parent=COORDINATOR, turns=(("completed", 40, 35),))
        self.assertIn("/", MOD["request_name"](review))
        store, t3, _ = fixture.write().read()
        placed = MOD["assign"](store, t3)
        self.assertEqual((placed[review].unit, placed[review].evidence.value), ("D7", "request"))
        fixture2_dir = tempfile.TemporaryDirectory()
        self.addCleanup(fixture2_dir.cleanup)
        fixture2 = Fixture(fixture2_dir.name)
        self.addCleanup(fixture2.close)
        fixture2.coordinator()
        fixture2.unit("D7", state="merged", thread=worker(1))
        fixture2.units[-1]["branch"] = "pstack-t3/other"
        fixture2.unit("D8", state="merged")
        fixture2.units[-1]["branch"] = "pstack-t3/other"
        fixture2.thread(worker(1), turns=(("completed", 90, 80),))
        review2 = double(delegated(COORDINATOR, request))
        fixture2.thread(review2, parent=COORDINATOR, turns=(("completed", 40, 35),))
        fixture2.write_store()
        stamp = fixture2.stamp(10)
        from tests.activity_tests.support import write_threads
        write_threads(fixture2, [{"at": stamp, "dish": "D8", "attempt": "1", "role": "reviewer", "thread": review2, "start": stamp, "end": stamp}])
        fixture2.write_database()
        store2, t32, _ = fixture2.read()
        placed2 = MOD["assign"](store2, t32)
        self.assertEqual((placed2[review2].unit, placed2[review2].evidence.value), ("D8", "record"))

    def test_merged_groups_are_collapsed_and_expandable(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", state="merged", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.unit("D8", state="in-progress", thread=worker(2))
        fixture.thread(worker(2), turns=(("running", 5, None),))
        from tests.activity_tests.pages import page_of
        page = page_of(fixture)
        by_id = {group.item.id if group.item else None: group for group in page.groups}
        self.assertTrue(by_id["D7"].collapsed)
        self.assertFalse(by_id["D8"].collapsed)
        data = MOD["wire"](page)
        self.assertEqual(data.get("C"), [group.collapsed for group in page.groups])
        self.assertTrue(any(data.get("C", [])))
        self.assertIn("collapsed", MOD["COLLAPSE_SCRIPT"])
        self.assertIn("collapsed", MOD["LIVE_FOLD"])

    def test_finished_items_still_fold_earlier_attempts(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", state="merged", thread=worker(3))
        fixture.retired("D7", worker(1))
        fixture.retired("D7", worker(2))
        fixture.thread(worker(3), turns=(("completed", 90, 80),))
        fixture.thread(worker(1), turns=(("completed", 100, 90),))
        fixture.thread(worker(2), turns=(("completed", 95, 85),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        from tests.activity_tests.support import write_threads
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": worker(1), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "worker", "thread": worker(2), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "3", "role": "worker", "thread": worker(3), "start": stamp, "end": stamp},
        ])
        fixture.write_database()
        store, t3, window = fixture.read()
        page = MOD["build_page"](store, t3, window, MOD["privacy_of"](store, t3))
        item = next(entry for entry in page.items if entry.id == "D7") if any(entry.id == "D7" for entry in page.items) else page.groups[0].item
        self.assertTrue(item.fold.summary)

    def test_model_colors_are_distinct_per_legend_with_other_shared_and_stable(self):
        self.assertIn(".p7", MOD["OVERFLOW_STYLE"])
        palette = MOD["palette_of"]((("model-a", 3), ("model-b", 2), ("model-c", 1), ("model-d", 1), ("model-e", 1), ("model-f", 1), ("model-g", 1), ("other model", 1)))
        slots = [palette[name] for name, _ in (("model-a", 3), ("model-b", 2), ("model-c", 1), ("model-d", 1), ("model-e", 1), ("model-f", 1), ("model-g", 1))]
        self.assertEqual(len(set(slots)), len(slots))
        self.assertEqual(palette["other model"], MOD["OTHER_SLOT"])
        again = MOD["palette_of"]((("model-a", 3), ("model-b", 2), ("model-c", 1), ("model-d", 1), ("model-e", 1), ("model-f", 1), ("model-g", 1), ("other model", 1)))
        self.assertEqual(palette, again)
