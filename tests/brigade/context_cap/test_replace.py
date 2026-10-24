if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

class CapReplaceMixin:
    def test_replace_refuses_a_malformed_stored_cap_before_raising_the_landing_floor(self):
        self.thread_landing()
        before = self.store_a_cap_that_is_not_a_whole_number()
        rows = self.landing_rows()
        error = self.brigade("set", "--thread", "t2", "--replace", code=1)
        self.assertIn("brigade: warning: restaurant.json contextCap '400000' is invalid: "
                      "it is not a whole number", error.splitlines())
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
        self.assertEqual(self.landing_rows(), rows)

    def test_replace_with_a_repair_flag_repairs_the_stored_cap_and_raises_the_landing_floor(self):
        self.thread_landing()
        self.store_a_cap_that_is_not_a_whole_number()
        self.brigade("set", "--thread", "t2", "--replace", "--context-cap", "400000")
        meta = self.meta()
        self.assertEqual((meta["thread"], meta["generation"], meta["contextCap"]), ("t2", 2, 400000))
        self.assertIn(("perf/", 2), self.landing_rows()["owner"])

    def test_replace_with_a_context_cap_raises_no_landing_floor_when_the_settings_read_refuses(self):
        self.thread_landing()
        self.write_settings("{broken")
        settings_before, store_before = self.settings().read_bytes(), self.store_files()
        rows = self.landing_rows()
        error = self.brigade("set", "--thread", "t2", "--replace", "--context-cap", "400000", code=1)
        self.assertEqual(error, f"brigade: {self.settings()} holds invalid JSON; fix or remove it")
        self.assertEqual(self.settings().read_bytes(), settings_before)
        self.assertEqual(self.store_files(), store_before)
        self.assertEqual(self.landing_rows(), rows)

    def test_replace_with_a_context_cap_on_valid_settings_replaces_the_thread_and_writes_the_cap(self):
        self.thread_landing()
        self.write_settings('{\n  "keep": true\n}\n')
        self.brigade("set", "--thread", "t2", "--replace", "--context-cap", "400000")
        meta = self.meta()
        self.assertEqual((meta["thread"], meta["generation"], meta["contextCap"]), ("t2", 2, 400000))
        self.assertIn(("perf/", 2), self.landing_rows()["owner"])
        self.assertEqual(self.settings().read_text(), '{\n  "keep": true,\n  "autoCompactWindow": 400000\n}\n')

