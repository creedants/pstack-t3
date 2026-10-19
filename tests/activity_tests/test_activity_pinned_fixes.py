import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests import test_activity
from tests.activity_tests.browser import PINNED, RENDER_TIMEOUT, TITLE, confined_run, select_browser
from tests.activity_tests.pages import CLEAN, DATA, PROGRAM, data_of, group, item, page, row
from tests.activity_tests.support import Fixture, worker

Fixture = test_activity.Fixture
MOD = test_activity.MOD
COORDINATOR = test_activity.COORDINATOR
delegated = test_activity.delegated


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


class PinnedBlockersTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fixture = Fixture(directory.name)
        self.addCleanup(self.fixture.close)

    def test_palette_ignores_legend_order_for_same_model_set(self):
        first = MOD["palette_of"]((("model-c", 2), ("model-d", 1)))
        second = MOD["palette_of"]((("model-d", 2), ("model-c", 1)))
        self.assertEqual(first, second)
        self.assertEqual(first["model-c"], 4)
        self.assertEqual(first["model-d"], 1)
        self.assertEqual(first["model-c"], MOD["model_slot"]("model-c"))

    def test_quiet_fold_summary_keeps_palette_slot_for_single_model(self):
        legend = (("model-c", 4), ("model-d", 2))
        palette = MOD["palette_of"](legend)
        self.assertEqual((palette["model-c"], palette["model-d"]), (4, 1))
        family = (
            MOD["Row"](0, "w1", "model-c", MOD["Status"]("done"), 10,
                       (MOD["Span"](0, 10, MOD["Status"]("done")),), None, 1, False),
            MOD["Row"](1, "d1", "model-d", MOD["Status"]("done"), 10,
                       (MOD["Span"](0, 10, MOD["Status"]("done")),), None, 1, False),
            MOD["Row"](1, "d2", "model-d", MOD["Status"]("done"), 10,
                       (MOD["Span"](0, 10, MOD["Status"]("done")),), None, 1, False),
        )
        made = MOD["summary"]((family[1], family[2]), 1, "2 sub-agents")
        self.assertEqual(made.model, "model-d")
        fixed = replace(made, color_slot=palette.get(made.model, made.color_slot))
        self.assertEqual(fixed.color_slot, 1)
        built = page(
            replace(group(item("D1", in_flight=True),
                          replace(row("w1", model="model-c"), color_slot=palette["model-c"]),
                          replace(row("d1", depth=1, model="model-d"), color_slot=palette["model-d"]),
                          replace(row("d2", depth=1, model="model-d"), color_slot=palette["model-d"])),
                    collapsed=False),
            hidden=MOD["Hidden"](),
        )
        built = replace(built, legend=legend)
        folded = MOD["fold_quiet_subagents"](built)
        summaries = [one for one in folded.groups[0].rows if one.stands_for > 1]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].model, "model-d")
        self.assertEqual(summaries[0].color_slot, palette["model-d"])
        wired = MOD["wire"](folded)
        slots = {name: slot for name, slot, _ in wired["P"]}
        self.assertEqual(slots["model-d"], summaries[0].color_slot)


class PinnedBrowserTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.browser, unavailable = select_browser(self.root)
        if self.browser is None:
            self.skipTest(f"no working headless browser: {'; '.join(unavailable)}")

    def test_overflow_colors_are_defined_with_six_tokens_and_wrapped_past_twelve(self):
        tokens = "".join(f"--chart-{number}:#{digit}{digit}{digit}{digit}{digit}{digit};" for number, digit in enumerate("123456", start=1))
        tokens += "--destructive:#b00000;--muted-foreground:#666666;--foreground:#111111;--background:#ffffff;--border:#e5e5e5;"
        legend = tuple((f"model-{name}", 1) for name in "abcdefg")
        palette = MOD["palette_of"](legend)
        self.assertGreater(max(palette.values()), 6)
        self.assertLessEqual(max(palette.values()), 12)
        rows = tuple(
            replace(row(f"w{name}", model=f"model-{name}"), color_slot=palette[f"model-{name}"])
            for name in "abcdefg"
        )
        groups = tuple(
            replace(group(item(f"D{at}", in_flight=True), rows[at]), collapsed=False)
            for at in range(7)
        )
        built = replace(page(*groups, hidden=MOD["Hidden"]()), legend=legend)
        document = MOD["render_html"](built, CLEAN)
        self.assertIn(".p7", document)
        probe = "".join((
            "<script>",
            "var dots=[].slice.call(document.querySelectorAll('#o .legend .dot'));",
            "var colors=dots.map(function(n){return getComputedStyle(n).backgroundColor;});",
            "var bars=[].slice.call(document.querySelectorAll('#o .tl .b'));",
            "var barColors=bars.map(function(n){return getComputedStyle(n).backgroundColor;});",
            "document.title=JSON.stringify({dots:colors,bars:barColors});",
            "</script>",
        ))
        target = self.root / "overflow.html"
        styled = document.replace("<style>", f"<style>:root{{{tokens}}}")
        target.write_text(styled.replace("<div id=o></div>", f"<div id=o></div>{PINNED}") + probe)
        import html as html_module
        drawn = confined_run(self.browser, target, self.root, "o", RENDER_TIMEOUT)
        seen = json.loads(html_module.unescape(TITLE.search(drawn).group(1)))
        self.assertEqual(len(seen["dots"]), 8)
        self.assertEqual(len(set(seen["dots"])), 8)
        self.assertNotIn("rgba(0, 0, 0, 0)", seen["dots"])
        self.assertNotIn("rgba(0, 0, 0, 0)", seen["bars"])
        big_legend = tuple((f"model-{number:02d}", 1) for number in range(15))
        big_palette = MOD["palette_of"](big_legend)
        self.assertLessEqual(max(big_palette.values()), 12)
        big_rows = tuple(
            replace(row(f"w{number:02d}", model=f"model-{number:02d}"), color_slot=big_palette[f"model-{number:02d}"])
            for number in range(15)
        )
        big_groups = tuple(
            replace(group(item(f"E{at}", in_flight=True), big_rows[at]), collapsed=False)
            for at in range(15)
        )
        big_built = replace(page(*big_groups, hidden=MOD["Hidden"]()), legend=big_legend)
        big_document = MOD["render_html"](big_built, CLEAN)
        big_target = self.root / "overflow-big.html"
        big_styled = big_document.replace("<style>", f"<style>:root{{{tokens}}}")
        big_target.write_text(big_styled.replace("<div id=o></div>", f"<div id=o></div>{PINNED}") + probe)
        big_drawn = confined_run(self.browser, big_target, self.root, "w", RENDER_TIMEOUT)
        big_seen = json.loads(html_module.unescape(TITLE.search(big_drawn).group(1)))
        self.assertNotIn("rgba(0, 0, 0, 0)", big_seen["dots"])
        self.assertNotIn("rgba(0, 0, 0, 0)", big_seen["bars"])

    def test_static_group_toggle_ignores_attempt_summary_and_respects_attempt_visibility(self):
        folded = MOD["AttemptFold"](
            summary="attempts 1-2: 2 workers, all ended",
            lines=(
                MOD["LedgerLine"]("1", "worker", "seat-1", False),
                MOD["LedgerLine"]("2", "worker", "seat-2", False),
            ),
            attempts=("1", "2"),
            ranks=((0, 1), (0, 2)),
            counts=(MOD["RoleCount"]("worker", 2, 0),),
        )
        current = MOD["Row"](0, "current", "model-a", MOD["Status"]("done"), 10,
                             (MOD["Span"](0, 10, MOD["Status"]("done")),), None, 1, False)
        first = replace(MOD["Row"](1, "earlier-1", "model-a", MOD["Status"]("done"), 10,
                                   (MOD["Span"](0, 10, MOD["Status"]("done")),), None, 1, False), earlier=True)
        second = replace(MOD["Row"](1, "earlier-2", "model-a", MOD["Status"]("done"), 10,
                                    (MOD["Span"](0, 10, MOD["Status"]("done")),), None, 1, False), earlier=True)
        merged = replace(item("D7", in_flight=False), fold=folded, lines=())
        grp = replace(group(merged, current, first, second), collapsed=True)
        built = replace(page(grp, hidden=MOD["Hidden"]()), legend=(("model-a", 3),))
        document = MOD["render_html"](built, CLEAN)
        self.assertIn("data-collapsed", MOD["COLLAPSE_SCRIPT"])
        probe = "".join((
            "<script>",
            "var head=document.querySelector('#o .tl .grp');",
            "var rows=[].slice.call(document.querySelectorAll('#o .tl .row'));",
            "var disp=function(){return rows.map(function(r){return getComputedStyle(r).display;});};",
            "var before=disp();",
            "head.click();",
            "var afterHead=disp();",
            "var details=document.querySelector('#o .tl .ldg details');",
            "var summaryOk=!!details;",
            "var afterSummary=null,openAfterSummary=null;",
            "if(details){",
            "details.querySelector('summary').click();",
            "afterSummary=disp();",
            "openAfterSummary=details.open;",
            "}",
            "var onclickKept=!!head.onclick;",
            "document.title=JSON.stringify({before:before,afterHead:afterHead,afterSummary:afterSummary,openAfterSummary:openAfterSummary,onclickKept:onclickKept,summaryOk:summaryOk});",
            "</script>",
        ))
        target = self.root / "collapse.html"
        target.write_text(document.replace("<div id=o></div>", f"<div id=o></div>{PINNED}") + probe)
        import html as html_module
        drawn = confined_run(self.browser, target, self.root, "c", RENDER_TIMEOUT)
        seen = json.loads(html_module.unescape(TITLE.search(drawn).group(1)))
        self.assertTrue(seen["onclickKept"])
        self.assertEqual(seen["before"], ["none", "none", "none"])
        self.assertEqual(seen["afterHead"][0], "grid")
        self.assertEqual(seen["afterHead"][1:], ["none", "none"])
        self.assertTrue(seen["summaryOk"])
        self.assertTrue(seen["openAfterSummary"])
        self.assertEqual(seen["afterSummary"][0], "grid")
