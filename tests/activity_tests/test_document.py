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
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock
import urllib.error
import urllib.request
from urllib.parse import quote, unquote
import html
import io
import random
import runpy
import select
import stat
import time
import fcntl
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from tests import in_process
import tests.test_activity as test_activity
from tests.activity_tests.support import ActivityCase, CHANGED, COORDINATOR, ELSEWHERE, Fixture, FROZEN, GUIDE, LOG_COLUMNS, MARKERS, MOD, NOW, OutputCase, PROJECT, PR6, PR7, ROOT, SCHEMA, SCRIPT, SKILL, UNIT_COLUMNS, VIEWS, busy, day, delegated, empty, extreme, failed_child, forms, frozen_bytes, live_listing, listing, native, one_running, populate, put, queued_only, setUpModule, tree, typical, widen_items, worker, write_goal_names, write_goals, write_threads
from tests.activity_tests.pages import BARE, CLEAN, DATA, FilterCase, HOSTILE, HOSTILE_DRAWN, HOSTILE_WRITTEN, NEEDLES, NodeRender, ODD, ORDINARY, PROGRAM, REPORT, REQUEST, ROW_FIELDS, LINK_FIELDS, ADDRESS, STORE_WORDS, STUB, UUID_TEXT, WORDS, agent, agents_shown, data_of, declarations, drawn, group, hostile, item, labels, named, ordinary, page, page_of, painted, row, same, skill_section, store_of, strings, tagged, T3_REFUSAL, STORE_REFUSAL
class DocumentTest(OutputCase):
    def test_default_window_is_3_hours_and_hours_changes_which_turns_are_on_the_page(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 200, 190), ("completed", 100, 90)))
        fixture.meta.pop("thread")
        fixture.write()
        for args, length, bars in (((), 10800, 1), (("--hours", "4"), 14400, 2)):
            with self.subTest(args):
                data = data_of(self.document(*args))
                self.assertEqual((data["w"][1], len(data["G"][0][1][0][6]) // 3), (length, bars))

    def test_data_object_holds_the_counts_legend_items_groups_and_notes(self):
        failed_child(self.fixture).write()
        data = data_of(self.document())
        self.assertEqual(sorted(data), ["C", "G", "I", "M", "N", "P", "S", "c", "k", "n", "v", "w"])
        self.assertEqual((data["v"], data["c"], data["n"], data["w"][1]), (1, "kit", [2, 2, 4, 1], 10800))
        self.assertEqual(data["S"], ["running", "queued", "waiting", "done", "failed", "stopped", "unknown"])
        self.assertEqual(data["P"], [["model-a", 1, 3], ["model-b", 3, 2], ["model-c", 4, 1]])
        for name, slot, _ in data["P"]:
            self.assertEqual(slot, MOD["model_slot"](name))
        self.assertEqual(data["M"], ["model-c", "model-a", "model-b"])
        self.assertEqual(data["I"], [["D6", "Queue fix", "landing", "warn", PR6, 1], ["D7", "Agent activity page", "in review", "info", PR7, 1],
                                     ["D5", "Old work", "merged", "", "", 0]])
        self.assertEqual([index for index, _ in data["G"]], [1, 2, -1])
        self.assertEqual([line[:5] + line[7:8] for line in data["G"][0][1]],
                         [[0, "worker", 1, 1, 0, 1], [1, "architect runner 2", 2, 3, 0, 1], [2, "spec_review", 2, 3, 3], [0, "review", 0, 4, 4]])
        self.assertEqual([line[5:6] + line[8:] for line in data["G"][0][1][:2]], [["63m", "42m"], ["6m", "6m"]])
        self.assertEqual([line[:2] + line[6][2:] + line[7:] for line in data["G"][2][1]], [[0, "why investigator", 3]])
        self.assertEqual([[bars[2::3] for bars in (line[6] for line in lines)] for _, lines in data["G"]], [[[0, 1], [1], [0], [2]], [[0]], [[3]]])
        self.assertEqual((data["k"][:3], data["k"][3][2::3]), ([0, 0, "15m"], [0, 0]))
        self.assertEqual(data["N"], ["1 agent is grouped by the name of the request that started it.", "1 other thread ran in this coordinator's T3 project outside this coordinator."])

    def test_checksum_in_the_document_is_fnv_1a_over_the_data_elements_utf16_units(self):
        fixture = one_running(self.fixture)
        fixture.units[0]["summary"] = "Agent activity page 𝔸é"
        fixture.write()
        document = self.document()
        value = 2166136261
        units = DATA.search(document).group(1).encode("utf-16-le")
        for at in range(0, len(units), 2):
            value = ((value ^ (units[at] | units[at + 1] << 8)) * 16777619) % 2 ** 32
        self.assertEqual(int(PROGRAM.search(document).group(2)), value)
        self.assertEqual((MOD["checksum"]("a"), MOD["checksum"]("foobar")), (0xE40C292C, 0xBF9CF968))

    def test_markup_a_quote_and_an_escape_character_in_the_store_name_a_summary_and_a_title_leave_no_backslash_or_angle_bracket_in_the_data_element(self):
        fixture = one_running(self.fixture)
        text, written = "x<script><img onerror=a(1)> \"q\" & p\x1b", "x&lt;script&gt;&lt;img onerror=a(1)&gt; &quot;q&quot; &amp; p "
        fixture.meta["restaurant"] = text
        fixture.units[0]["summary"] = text
        fixture.thread(delegated(worker(1), "helper-task"), title=text, model="<b>\\\"model\x1b", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.write()
        raw = DATA.search(self.document()).group(1)
        self.assertEqual([character for character in "\\<>" if character in raw], [])
        data = json.loads(raw)
        self.assertEqual((data["c"], data["I"][1][1], data["G"][0][1][1][1], data["M"]),
                         (written.strip(), written.strip(), written.strip(), ["model-a", "&lt;b&gt;&#92;&quot;model"]))

    def test_data_element_of_a_page_whose_strings_hold_markup_a_quote_a_backslash_a_tab_and_u2028_holds_no_backslash_or_angle_bracket(self):
        raw = DATA.search(MOD["render_html"](hostile(), same)).group(1)
        self.assertEqual([character for character in "\\<>" if character in raw], [])
        data = json.loads(raw)
        self.assertEqual((data["c"], data["I"], data["G"][0][1][0][1], data["M"]),
                         (HOSTILE_WRITTEN, [["D1 " + HOSTILE_WRITTEN, HOSTILE_WRITTEN, "working", "go", "", 0]], HOSTILE_WRITTEN, ["model " + HOSTILE_WRITTEN]))

    def test_entities_writes_five_characters_as_entities_and_each_control_character_u2028_and_u2029_as_one_space(self):
        self.assertEqual(MOD["entities"]("&<>\"\\'"), "&amp;&lt;&gt;&quot;&#92;'")
        self.assertEqual(MOD["entities"]("&amp;&#92;"), "&amp;amp;&amp;#92;")
        controls = "".join(map(chr, [*range(0x20), *range(0x7F, 0xA0), 0x2028, 0x2029]))
        self.assertEqual(MOD["entities"](controls), " " * 67)
        self.assertEqual(MOD["entities"](" ~\xa0\u2027\u202a"), " ~\xa0\u2027\u202a")

    def test_encode_writes_each_string_value_as_entities_writes_it_and_leaves_numbers_null_and_keys(self):
        self.assertEqual(MOD["encode"]({"<": ["</script>\t", 1, None, {"b": "\"\\"}]}, same), '{"<":["&lt;/script&gt; ",1,null,{"b":"&quot;&#92;"}]}')

    def test_encode_writes_a_string_in_a_tuple_as_entities_writes_it(self):
        self.assertEqual(MOD["encode"]({"a": ("<",)}, same), '{"a":["&lt;"]}')

    def test_no_invented_id_or_path_is_in_the_document_or_the_text(self):
        for build in (failed_child, busy):
            with self.subTest(build.__name__):
                fixture = self.fixture = Fixture(self.fixture.root / build.__name__)
                build(fixture).write()
                for output in (self.document(), self.out("--text"), self.document("--max-bytes", "500000")):
                    self.assertEqual([marker for marker in MARKERS if marker in output], [])

    def test_each_store_state_reads_as_its_plain_word_and_no_store_word_is_in_the_page_or_the_text(self):
        fixture = self.fixture
        fixture.coordinator()
        states = ("in-progress", "in-review", "passed", "queued", "sent-back", "blocked", "merged", "dropped", "plated")
        for number, state in enumerate(states, start=1):
            fixture.unit(f"D{number}", state, f"summary {number}", thread=worker(number))
            fixture.thread(worker(number), turns=(("completed", 100 - number, 99 - number),))
        fixture.write()
        data, text = data_of(self.document()), self.out("--text")
        words = ["working", "in review", "passed review", "landing", "sent back", "blocked", "merged", "dropped", "other"]
        self.assertEqual([item[2] for item in sorted(data["I"], key=lambda item: item[0])], words)
        for word in words:
            self.assertIn(f"   {word}   summary", text)
        for output in (" ".join(strings(list(data.values()))), text, MOD["STYLE"], MOD["RENDERER"]):
            self.assertEqual(STORE_WORDS.findall(output), [])
            self.assertEqual([state for state in ("in-progress", "in-review", "sent-back", "plated") if state in output], [])

    def test_renderer_source_holds_no_call_that_parses_a_string_as_html_or_code(self):
        self.assertEqual([call for call in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "Function(", "setTimeout", "\"", "`") if call in MOD["RENDERER"]], [])
        self.assertIn(MOD["UNGROUPED"], MOD["RENDERER"])

    def test_stylesheet_has_no_rule_for_html_body_root_or_every_element_sets_only_font_and_color_on_the_outermost_one_and_gives_the_threads_background_only_to_a_header_row(self):
        declared = declarations(MOD["STYLE"])
        whole_page = re.compile(r"(html|body|:root)(?![\w-])|\*")
        self.assertEqual([selector for selector in ("html", "body>div", ":root", "*", "#o", ".html", "bodyguard") if whole_page.match(selector)], ["html", "body>div", ":root", "*"])
        self.assertEqual([selector for selector, _, _ in declared if whole_page.match(selector)], [])
        self.assertEqual([(name, value) for selector, name, value in declared if selector == "#o"], [("font", "13px/1.4 var(--font-sans)"), ("color", "var(--foreground)")])
        self.assertEqual([selector for selector, _, value in declared if "var(--background)" in value], [".grp"])

    def test_narrow_rule_puts_each_rows_model_on_a_second_line_of_a_30_pixel_row_and_hides_only_every_other_axis_label_and_the_wide_rule_gives_the_model_up_to_half_the_label_column(self):
        narrow = dict(re.findall(r"([^{}]+)\{([^{}]*)\}", re.search(r"@media\(max-width:520px\)\{(.*)\}$", MOD["STYLE"]).group(1)))
        self.assertEqual([selector for selector, body in narrow.items() if "display:none" in body], [".axis i:nth-child(odd)"])
        self.assertEqual(narrow[".row"], "position:relative;height:30px;align-items:start")
        self.assertEqual(narrow[".l small"], "position:absolute;left:0;right:0;bottom:0;max-width:none;line-height:13px")
        self.assertEqual((narrow[".d1 small"], narrow[".d2 small"]), ("left:14px", "left:28px"))
        wide = MOD["STYLE"].split("@media")[0]
        self.assertEqual(re.findall(r"[^{}]*small[^{}]*\{[^{}]*\}", wide), ["h2,small,.stat span,.legend,.axis,.sum,.foot,.co,.chip{color:var(--muted-foreground)}",
                                                                              ".sum,.l span,.l small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
                                                                              ".l small{flex:none;max-width:50%}", "small{font-size:10.5px}"])
        self.assertIn(".now div,.strip>*,.grp,.l{display:flex;gap:8px;align-items:baseline;min-width:0}", wide)
        self.assertEqual(re.findall(r"height:[^;}]*", MOD["STYLE"]), ["height:1.1", "height:8px", "height:9px", "height:16px", "height:19px", "height:100%", "height:11px", "height:30px", "height:13px"])

    def test_base_stylesheet_names_a_color_in_a_color_background_border_or_c_declaration_only_as_a_variable_transparent_or_inherit_and_reads_only_theme_variables_and_its_own_two(self):
        style = MOD["STYLE_BASE"]
        declared = declarations(style)
        self.assertEqual(re.findall(r"[0-9](?:vh|vw)\b|#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", style), [])

        def other_words(value):
            plain = r"var\(--[\w-]+\)|[0-9.]+(px|%|deg)?|solid|transparent|inherit|repeating-linear-gradient"
            return [word for word in re.findall(r"var\([^)]*\)|[\w.%-]+", value) if not re.fullmatch(plain, word)]

        self.assertEqual(other_words("1px solid red"), ["red"])
        self.assertEqual(other_words("repeating-linear-gradient(135deg,var(--c) 0 4px,canvas 4px 7px)"), ["canvas"])
        colored = [(name, value) for _, name, value in declared if name in ("color", "--c") or name.startswith(("background", "border"))]
        self.assertEqual([(name, value) for name, value in colored if other_words(value)], [])
        allowed = {"--foreground", "--muted-foreground", "--background", "--border", "--success", "--destructive", "--warning", "--info", "--radius", "--font-sans", "--font-mono"}
        allowed |= {f"--chart-{number}" for number in range(1, 7)}
        own = set(re.findall(r"(--[\w-]+):", style))
        self.assertEqual(own, {"--c", "--lab"})
        self.assertEqual(set(re.findall(r"var\((--[\w-]+)", style)) - own - allowed, set())
        self.assertEqual(re.findall(r"@media[^{]*", style), ["@media(max-width:520px)"])
