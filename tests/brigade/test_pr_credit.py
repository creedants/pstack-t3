if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import CLAUDE, CODEX, BrigadeTest, json, setUpModule

DEEPSEEK = "opencode-go/deepseek-v4.1-flash"


class PrCreditTest(BrigadeTest):
    """prCredit is recorded at open and set, and credit prints the line the recorded rows support."""

    def meta(self, name="perf"):
        return json.loads((self.store / "bridge-kit" / name / "restaurant.json").read_text())

    def detect(self):
        return json.loads(self.brigade("onboard", "--project-root", str(self.project), "--name", "Perf",
                                       "--detect"))

    def test_open_records_pr_credit_on_and_a_plain_open_records_no_key(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--pr-credit", "on")
        self.assertIs(self.meta()["prCredit"], True)
        self.brigade("open", "--project-root", str(self.project), "--name", "Other")
        self.assertNotIn("prCredit", self.meta("other"))

    def test_set_pr_credit_off_then_on_leaves_false_then_true(self):
        self.open()
        self.brigade("set", "--pr-credit", "off")
        self.assertIs(self.meta()["prCredit"], False)
        self.brigade("set", "--pr-credit", "on")
        self.assertIs(self.meta()["prCredit"], True)

    def test_set_pr_credit_yes_refuses_and_leaves_restaurant_json_byte_for_byte(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        self.assertEqual(self.brigade("set", "--pr-credit", "yes", code=1),
                         "brigade: --pr-credit takes on or off, got 'yes'")
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_open_pr_credit_maybe_refuses_and_creates_no_store(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Other", "--pr-credit", "maybe",
                     code=1)
        self.assertFalse((self.store / "bridge-kit" / "other").exists())

    def test_reopening_an_off_store_with_pr_credit_on_prints_the_stays_line_and_keeps_off(self):
        self.open()
        self.brigade("set", "--pr-credit", "off")
        out = self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--pr-credit", "on")
        self.assertIn("pr-credit stays off; change it with set --pr-credit", out)
        self.assertIs(self.meta()["prCredit"], False)

    def test_credit_on_names_one_author_and_one_verifier(self):
        self.open()
        self.brigade("set", "--pr-credit", "on")
        self.fire_one()
        self.record("a" * 40, "pass")
        self.assertEqual(self.brigade("credit", "D1", "--coordinator", CLAUDE),
                         f"Coordinated by {CLAUDE}. Written by {CLAUDE}. Reviewed by {CODEX}.")

    def test_credit_names_each_author_in_first_recorded_order_and_names_a_verifier_once(self):
        self.open()
        self.brigade("set", "--pr-credit", "on")
        self.fire_one()
        self.record("a" * 40, "send-back", author=CLAUDE, verifier=CODEX)
        self.record("b" * 40, "pass", author=DEEPSEEK, verifier=CODEX)
        self.assertEqual(self.brigade("credit", "D1"),
                         f"Written by {CLAUDE}, {DEEPSEEK}. Reviewed by {CODEX}.")

    def test_credit_names_a_panel_member_rows_verifier(self):
        self.open()
        self.brigade("set", "--pr-credit", "on")
        self.fire_one()
        self.record("a" * 40, "pass", "--member", verifier=DEEPSEEK)
        self.assertEqual(self.brigade("credit", "D1"),
                         f"Written by {CLAUDE}. Reviewed by {DEEPSEEK}.")

    def test_credit_with_no_pass_rows_names_only_the_coordinator(self):
        self.open()
        self.brigade("set", "--pr-credit", "on")
        self.fire_one()
        self.assertEqual(self.brigade("credit", "D1", "--coordinator", CLAUDE),
                         f"Coordinated by {CLAUDE}.")

    def test_credit_with_no_pass_rows_and_no_coordinator_prints_no_text(self):
        self.open()
        self.brigade("set", "--pr-credit", "on")
        self.fire_one()
        self.assertEqual(self.brigade("credit", "D1"), "")

    def test_credit_off_and_never_recorded_print_the_off_line(self):
        self.open()
        self.fire_one()
        self.assertEqual(self.brigade("credit", "D1"),
                         "credit: off for this restaurant; set --pr-credit on to turn it on")
        self.brigade("set", "--pr-credit", "off")
        self.assertEqual(self.brigade("credit", "D1"),
                         "credit: off for this restaurant; set --pr-credit on to turn it on")

    def test_credit_unknown_item_refuses_even_when_pr_credit_is_off(self):
        self.open()
        self.assertEqual(self.brigade("credit", "D9", code=1), "brigade: no D9 in dishes.tsv")
        self.brigade("set", "--pr-credit", "on")
        self.assertEqual(self.brigade("credit", "D9", code=1), "brigade: no D9 in dishes.tsv")

    def test_onboard_reports_pr_credit_missing_then_configured(self):
        self.open()
        self.assertEqual(self.detect()["facts"]["pr_credit"], {
            "state": "missing", "value": "", "fix": "open --pr-credit on or set --pr-credit on"})
        self.brigade("set", "--pr-credit", "on")
        self.assertEqual(self.detect()["facts"]["pr_credit"], {
            "state": "configured", "value": True, "fix": "open --pr-credit on or set --pr-credit on"})
