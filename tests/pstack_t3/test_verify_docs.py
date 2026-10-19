import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

from tests.pstack_t3.support import pointer_sentence, runtime_docs, runtime_doc_section, override_text


class VisualReportsDocTest(unittest.TestCase):
    def setUp(self):
        self.section = runtime_doc_section("verification.md", "## Visual reports")

    def test_section_sits_after_verification_surfaces(self):
        text = (ROOT / "t3/runtime.md").read_text()
        self.assertLess(text.index("## Verification surfaces"), text.index("## Visual reports"))
        self.assertLess(text.index("## Visual reports"), text.index("## History"))
        bullet = next(line for line in runtime_docs().splitlines() if line.startswith("- Web or Electron UI:"))
        self.assertNotIn("html_", bullet)

    def test_vocabulary_row_points_at_the_section(self):
        text = (ROOT / "t3/runtime.md").read_text()
        row = next(line for line in text.splitlines() if line.startswith("| status page, dashboard, report table, chart |"))
        self.assertIn("`html_preview`, then `html_render`, inside a reply already due", row)
        self.assertIn("[Visual reports](#visual-reports)", row)

    def test_page_only_rides_a_reply_already_due(self):
        for sentence in (
            "A page is for a reply that is already due.",
            "A short status with no table stays text.",
            "A rendered page is a reply the user reads, even with no reply text.",
            "Render none on a wake that sends no reply.",
            "Brigade at `digest` renders no page except the one [Agent activity](../brigade/SKILL.md#agent-activity) names, "
            "so its replies stay in the plain form [Digest messages](../brigade/SKILL.md#digest-messages) sets.",
        ):
            self.assertIn(sentence, self.section)

    def test_preview_render_and_text_fallback(self):
        for sentence in (
            "Call `html_preview` with it. Fix every console error and every clipped or overlapping element.",
            "Call `html_render` with the document, a `title`, and the preview's `contentHeight` as `height`, raised to 80 or capped at 2000.",
            "Every decision that waits on the user, every PR link, and the store and report paths stay in the reply text",
            "Leave `html`, `body`, and the outermost element with no background color",
            "When either tool is missing or `html_render` fails, send the same facts as text.",
        ):
            self.assertIn(sentence, self.section)

    def test_orchestrate_reply_keeps_upstream_facts_and_links_the_section(self):
        text = override_text("poteto-mode/playbooks/orchestrate.md")
        reply = next(line for line in text.splitlines() if line.startswith("**Reply:**"))
        self.assertTrue(reply.startswith(
            "**Reply:** at checkpoints and close: the predicate and the count against it from `units.tsv` and `ledger.tsv`, "
            "tracks and what each landed, the frontier (PR list plus SHAs), verdicts summary, what was abandoned and why, "
            "gates awaiting the human (the only asks), the store path, and the trail path. "
            "Numbers from the tables, not narrative. Include PR links."
        ))
        self.assertIn("per [Visual reports](../../pstack-runtime/SKILL.md#visual-reports).", reply)
        self.assertIn("The gates, PR links, store path, and trail path stay in the reply text.", reply)

class NoCommentsReadOnlyDocTest(unittest.TestCase):
    def setUp(self):
        self.text = override_text("no-comments/SKILL.md")
        self.scope = self.text.split("## Scope", 1)[1].split("\n## Steps", 1)[0]

    def test_read_only_caller_never_runs_the_skill(self):
        for sentence in (
            "This skill edits files and delegates edits.",
            "A caller working under a read-only instruction never runs it and spawns no Comment Sicko, whatever its role label.",
            "per [the runtime's Permissions section](../pstack-runtime/SKILL.md#permissions).",
            "Step 1's `role: \"review\"` is a label and does not make Comment Sicko read-only.",
        ):
            self.assertIn(sentence, self.scope)

    def test_guard_leads_the_scope_and_keeps_the_pointer_once(self):
        self.assertLess(self.scope.index("This skill edits files"), self.scope.index("Use the caller's files or diff."))
        self.assertEqual(self.text.count(pointer_sentence("skill")), 1)

    def test_authorized_writing_flow_is_unchanged(self):
        self.assertIn('Spawn Comment Sicko as a fresh child with `delegate_task` (`role: "review"`', self.text)
        self.assertIn("It edits comments in the shared checkout, so run it alone", self.text)
        self.assertNotIn("readonly", self.scope)


if __name__ == "__main__":
    unittest.main()
