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

try:
    import tests.test_activity as test_activity
except ImportError:
    import test_activity

Fixture = test_activity.Fixture
MOD = test_activity.MOD
COORDINATOR = test_activity.COORDINATOR
delegated = test_activity.delegated
worker = test_activity.worker


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


class ReviewerGroupingTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fixture = Fixture(directory.name)
        self.addCleanup(self.fixture.close)

    def test_review_named_by_branch_tail_groups_under_item(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D466", thread=worker(1))
        fixture.units[-1]["branch"] = "pstack-t3/d468"
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        review = delegated(COORDINATOR, "brigade-pstack-t3-pstack-t3/pstack-t3/d468-review-4")
        fixture.thread(review, parent=COORDINATOR, turns=(("completed", 40, 35),))
        store, t3, window = fixture.write().read()
        placed = MOD["assign"](store, t3)
        self.assertEqual((placed[review].unit, placed[review].evidence.value), ("D466", "request"))
        page = MOD["build_page"](store, t3, window, MOD["privacy_of"](store, t3))
        groups = {group.item.id if group.item else None: [row.label for row in group.rows] for group in page.groups}
        self.assertIn("D466", groups)
        self.assertEqual(page.hidden.by_request_name, 1)

    def test_review_named_by_id_groups_under_item(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7")
        fixture.units[-1]["branch"] = "pstack-t3/other"
        request = "brigade-kit-d7-verify-0a1b2c3"
        review = delegated(COORDINATOR, request)
        fixture.thread(review, parent=COORDINATOR, turns=(("completed", 40, 35),))
        store, t3, _ = fixture.write().read()
        placed = MOD["assign"](store, t3)
        self.assertEqual((placed[review].unit, placed[review].evidence.value), ("D7", "request"))

    def test_review_matching_two_items_groups_under_none(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7")
        fixture.unit("D8")
        review = delegated(COORDINATOR, "d7-d8-review")
        fixture.thread(review, parent=COORDINATOR, turns=(("completed", 40, 35),))
        store, t3, _ = fixture.write().read()
        placed = MOD["assign"](store, t3)
        self.assertEqual((placed[review].unit, placed[review].evidence.value), (None, "none"))

    def test_review_matching_no_item_groups_under_none(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7")
        fixture.units[-1]["branch"] = "pstack-t3/d7"
        review = delegated(COORDINATOR, "brigade-kit-zzz-verify")
        fixture.thread(review, parent=COORDINATOR, turns=(("completed", 40, 35),))
        store, t3, _ = fixture.write().read()
        placed = MOD["assign"](store, t3)
        self.assertEqual((placed[review].unit, placed[review].evidence.value), (None, "none"))

    def test_recorded_reviewer_thread_wins_over_branch_match(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", task="my-task")
        fixture.unit("D8")
        fixture.units[-1]["branch"] = "x/my-task"
        review = delegated(COORDINATOR, "my-task")
        fixture.thread(review, parent=COORDINATOR, turns=(("completed", 40, 35),))
        store, t3, _ = fixture.write().read()
        placed = MOD["assign"](store, t3)
        self.assertEqual((placed[review].unit, placed[review].evidence.value), ("D7", "record"))
