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
from tests import test_activity
from tests.activity_tests.support import ActivityCase, CHANGED, COORDINATOR, ELSEWHERE, Fixture, FROZEN, GUIDE, LOG_COLUMNS, MARKERS, MOD, NOW, OutputCase, PROJECT, PR6, PR7, ROOT, SCHEMA, SCRIPT, SKILL, UNIT_COLUMNS, VIEWS, busy, day, delegated, empty, extreme, failed_child, forms, frozen_bytes, live_listing, listing, native, one_running, populate, put, queued_only, setUpModule, tree, typical, widen_items, worker, write_goal_names, write_goals, write_threads
from tests.activity_tests.pages import BARE, CLEAN, DATA, FilterCase, HOSTILE, HOSTILE_DRAWN, HOSTILE_WRITTEN, NEEDLES, NodeRender, ODD, ORDINARY, PROGRAM, REPORT, REQUEST, ROW_FIELDS, LINK_FIELDS, ADDRESS, STORE_WORDS, STUB, UUID_TEXT, WORDS, agent, agents_shown, data_of, declarations, drawn, group, hostile, item, labels, named, ordinary, page, page_of, painted, row, same, skill_section, store_of, strings, tagged, T3_REFUSAL, STORE_REFUSAL
class FilterPathTest(FilterCase):
    def test_clean_removes_an_absolute_path_of_two_or_more_segments_a_home_path_a_drive_path_and_a_file_address_and_keeps_a_relative_path(self):
        gone = ("/pathmarker/notes.md", "/a/b", "/home/someone/x", "(/pathmarker/a)", "x=/pathmarker/a", "a:/pathmarker/a", "/pathmarker//a", "/.config/a", "/~x/a", "/path-marker+1/a", "/pathmarker/a/",
                "~/x", "~/pathmarker", "~user/pathmarker", "$HOME/pathmarker", "${HOME}/pathmarker", "~\\pathmarker",
                "C:\\Users\\private\\file.txt", "c:/Users/private", "C:\\x", "\\\\server\\share", "file:///home/private/secret", "file:/home/private/secret", "FILE:C:/x", "x=file:~/a", "file:secret.txt",
                "file:/secret", "%2Fpathmarker%2Fnotes.md", "%252Fpathmarker%252Fa", "</pathmarker/a>", ">/pathmarker/a")
        for word in gone:
            with self.subTest(word):
                self.assertEqual(self.clean(f"see {word} now"), "see now")
        kept = "see src/a.py and/or docs/guide.md with opencode-go/model at https://example.test/o/r/pull/7 for I/O 24/7 +/- ~ ~5 ~1u/x ${HOME/x $HOME}/x a\\b é:\\x Makefile:12 profile:x file: x 1/2 ./a ../b now"
        self.assertEqual(self.clean(kept), kept)
    def test_clean_keeps_a_one_segment_word_that_starts_with_a_slash_wherever_a_path_could_start(self):
        kept = ("/review", "/loop", "/code-review", "/pathmarker", "/secret", "/tmp", "/review.", "/review,", "(/review)", "x=/review", "key:/review", "</review>", ">/review", "/review/",
                "\"/review\"", "'/review'/'/loop'", "`/loop`", "“/code-review”", "%2Freview", "%252Freview", "/review:", "/REVIEW", "/review_2", "/.hidden", "/~x")
        for word in kept:
            with self.subTest(word):
                self.assertEqual(self.clean(f"run {word} now"), f"run {word} now")
        text = "Run /review then /loop and /code-review, or \"/review\" twice"
        self.assertEqual(self.clean(text), text)
        self.assertEqual(self.clean("/review /loop /a/b /code-review"), "/review /loop /code-review")
        self.assertEqual(self.clean("see /srv/a b/c /loop d/e and /srv/f /g/h /review now"), "see /loop d/e and /review now")
    def test_clean_removes_a_one_segment_path_it_was_given_in_any_case_and_keeps_another_one_segment_word(self):
        paths = {"/storemarker", "/checkoutmarker"}
        self.assertEqual(self.clean("read /storemarker and /CHECKOUTMARKER and x/storemarker/a and %2Fcheckoutmarker then /review and /loop end", paths=paths), "read and and and then /review and /loop end")
        self.assertEqual(self.clean("read /storemarker and /checkoutmarker end"), "read /storemarker and /checkoutmarker end")
    def test_clean_removes_a_path_of_two_or_more_segments_whose_first_starts_with_a_hyphen_a_plus_sign_an_asterisk_an_at_sign_or_a_second_slash_and_keeps_one_segment_two_slashes_alone_and_two_slashes_after_a_colon(self):
        for word in ("/-secret/notes.txt", "/+secret/notes.txt", "//secret/share/notes.txt", "///a/b", "x=//secret/a", "(/*/a)", "/@a/b", "%2F-secret%2Fnotes.txt"):
            with self.subTest(word):
                self.assertEqual(self.clean(f"see {word} now"), "see now")
        kept = "see /review and /-flag with /+1 or // and // note at https://example.test/o/r/pull/7 for ftp://secret/a and a//b then /- / -/ now"
        self.assertEqual(self.clean(kept), kept)
    def test_clean_removes_a_path_it_was_given_of_fewer_than_4_characters_only_as_a_whole_word_that_starts_with_a_slash_where_a_path_can_start_with_or_without_closing_marks_after_it(self):
        for word in ("/x", "/X", "/x.", "/x/", "(/x)", "x=/x", "\"/x\"", "/x/src", "/x/).", "%2Fx", "/x?!"):
            with self.subTest(word):
                self.assertEqual(self.clean(f"in {word} now", paths={"/x"}), "in now")
        kept = "see /x.py and /xy with /x-ray in src/x.py or /review at https://example.test/x/r/pull/7 and https://x/o/r/pull/7 for a/x and x/ then /x(1) now"
        self.assertEqual(self.clean(kept, paths={"/x"}), kept)
        self.assertEqual(self.clean("in /x and /X/ or /x, then /xy now", paths={"/x/"}), "in and or then /xy now")
        self.assertEqual(self.clean("at / and (/) or // then /a and https://example.test/o/r/pull/7 now", paths={"/"}), "at and or then /a and https://example.test/o/r/pull/7 now")
        self.assertEqual(self.clean("in /x. and /x.. or /x now", paths={"/x."}), "in and or /x now")
        self.assertEqual(self.clean("in /x and /xy now"), "in /x and /xy now")
    def test_clean_removes_a_path_after_a_quotation_mark_up_to_the_next_such_mark_and_any_other_through_each_next_word_with_a_slash(self):
        self.assertEqual(self.clean('see "/srv/private dir/secret file.txt" and `~/a b` and \'C:\\a b\\c d\' now'), "see and and now")
        self.assertEqual(self.clean("see “/srv/private dir/secret file.txt” and ‘~/a b’ and x=”/srv/a now"), "see and and now")
        self.assertEqual(self.clean('see "/srv/private dir/secret file.txt and more'), "see file.txt and more")
        self.assertEqual(self.clean("see /srv/private dir/secret file.txt now"), "see file.txt now")
        self.assertEqual(self.clean("see file:/home/private/My Documents/n.txt and file:~/My Documents/n.txt now"), "see and now")
        self.assertEqual(self.clean("see /srv/a then b/c d/e and f/g"), "see then b/c d/e and f/g")
    def test_clean_writes_each_control_character_as_a_space_before_it_looks_and_drops_a_character_utf8_cannot_hold(self):
        ids = {"two_words here"}
        for character in ("\x00", "\x1b", "\x7f", "\x80", "\t", "\n", "\u2028"):
            with self.subTest(repr(character)):
                self.assertEqual(self.clean(f"a two_words{character}here b", ids), "a b")
                self.assertEqual(self.clean(f"a{character}b"), "a b")
        self.assertEqual(self.clean("a two_wo\udc80rds here b mcp\ud800:x", ids), "a b")
        self.assertEqual(self.clean("a tab\there_id b tab here_id c TAB\x00HERE_ID d", {"tab\there_id"}), "a b c d")
    def test_clean_removes_text_that_is_private_only_as_the_document_writes_it(self):
        self.assertEqual(self.clean("a <private_thread9 b", {"lt;private_thread9"}), "a b")
        self.assertEqual(self.clean('a "x b', {"quot;x"}), "a b")
        self.assertEqual(MOD["entities"](self.clean("R&D <b> \"q\" a\\b")), "R&amp;D &lt;b&gt; &quot;q&quot; a&#92;b")
    def test_clean_repeats_until_no_word_holds_private_text_keeps_the_spaces_before_the_first_word_and_gives_an_empty_text_after_8_passes(self):
        ids = {"left_1 right_2"}
        self.assertEqual(self.clean("  D7 worker   mcp:x   running for 5m"), "  D7 worker   running for 5m")
        self.assertEqual(self.clean("  mcp:x worker   node:y"), "  worker")
        self.assertEqual(self.clean("say left_1 /pathmarker/a right_2 end", ids), "say end")
        nested = " ".join(["left_1"] * 7 + ["/pathmarker/a"] + ["right_2"] * 7)
        self.assertEqual(self.clean(f"say {nested} end", ids), "say end")
        deep = " ".join(["left_1"] * 8 + ["/pathmarker/a"] + ["right_2"] * 8)
        self.assertEqual(self.clean(f"say {deep} end", ids), "")
        self.assertEqual(self.clean(""), "")
    def test_no_string_clean_returns_holds_private_text_as_written_or_as_the_document_writes_it(self):
        child = delegated(COORDINATOR, "kit-d7-audit")
        privacy = MOD["Privacy"]({child, COORDINATOR, "sess_Ab12Cd34Ef56", "lt;private_thread9"}, {"/srv/pathmarker state/proj"})
        pieces = ("queue", "D7", child, quote(child, safe=""), COORDINATOR.upper(), "<private_thread9", "/srv/pathmarker", "state/proj/x", "a@b.co", UUID_TEXT, "node:x", "%2Fa%2Fb",
                  "file:x", "\x00", "&", "\"", "~/a", "sess_Ab12Cd34Ef56", "%", "3A", "mcp", ":", "x", "src/a.py", "'", "C:\\a")
        state = 7
        for _ in range(400):
            words = []
            for _ in range(6):
                state = state * 1103515245 + 12345 & 0x7FFFFFFF
                words.append(pieces[state % len(pieces)] + ("" if state & 64 else " "))
            cleaned = privacy.clean("".join(words))
            with self.subTest(cleaned):
                self.assertEqual((privacy.private(cleaned), privacy.clean(cleaned), MOD["UNPRINTED"].search(cleaned)), ([], cleaned, None))
    def test_unescaped_equals_urllib_unquote_and_names_the_source_span_of_each_character(self):
        for text in ("", "plain", "a%20b", "%E2%82%AC5", "%e2%82%ac", "%zz%4", "100%", "%41%42C%44", "%C3%28", "caf%C3%A9 %F0%9D%94%B8", "%2541"):
            with self.subTest(text):
                self.assertEqual(MOD["unescaped"](text)[0], unquote(text))
        self.assertEqual(MOD["unescaped"]("a%41%42c"), ("aABc", [(0, 1), (1, 7), (1, 7), (7, 8)]))
    def test_field_is_the_first_line_with_single_spaces_as_clean_leaves_it(self):
        field = MOD["Privacy"]({"two_words here"}).field
        self.assertEqual(field("  Fix   the queue \n second line"), "Fix the queue")
        self.assertEqual(field("a two_words    here b and /pathmarker/x c"), "a b and c")
        self.assertEqual(field(""), "")
    def test_model_is_the_name_after_one_provider_prefix_cut_to_48_characters_and_other_model_when_nothing_is_left(self):
        model = MOD["Privacy"]({worker(1)}).model
        kept = ("model-a", "gpt-6.1-sol", "claude-sonnet-4-5-20250929", "default", "k3", "a" * 48, "Model-A", "model_a", "GPT 5 (high)", "opencode-go/deepseek-v4.1-flash", "vendor/model-b",
                "queue", "code", "review", "src", "private auditor", "451237", "model-0a1b2c3", "модель")
        self.assertEqual([model(name) for name in kept], list(kept))
        self.assertEqual((model("opencode/muse-spark-1.3-free"), model("claudeAgent/model-b"), model(" model-a\nsecond"), model("a" * 49)), ("muse-spark-1.3-free", "model-b", "model-a", "a" * 47 + "…"))
        other = ("mcp:alpha-secret", "node:alpha-secret", "run:alpha-secret", "user@example.test", "C:\\Users\\private\\file.txt", "file:/home/private/secret", "/pathmarker/model", worker(1), UUID_TEXT, "", " ")
        self.assertEqual([name for name in other if model(name) != "other model"], [])
    def test_link_is_the_https_pull_request_address_without_userinfo_query_or_fragment_a_blank_value_gives_neither_and_any_other_value_is_refused(self):
        link = MOD["Privacy"]({"sess_Ab12Cd34Ef56", worker(1)}).link
        address = "https://git.example/Owner.x/re_po-1/pull/123456"
        for value in (address, "https://user:pw@git.example/Owner.x/re_po-1/pull/123456", address + "?x=mcp:alpha-secret", address + "#task:alpha-secret",
                      "HTTPS://GIT.EXAMPLE/Owner.x/re_po-1/pull/123456", "https://git.example/Owner.x/re_po-1/pull/123456?", f"https://{quote(worker(1), safe='')}@git.example/Owner.x/re_po-1/pull/123456"):
            with self.subTest(value):
                self.assertEqual(link(value), (address, ""))
        for value in ("https://git.example/queue/code/pull/451237", "https://451237.example/review/src/pull/7", "https://git.example/kit-d7-audit/0a1b2c3/pull/" + "7" * 400):
            with self.subTest(value):
                self.assertEqual(link(value), (value, ""))
        self.assertEqual((link(""), link("  ")), (("", ""), ("", "")))
        refused = ("http://git.example/o/r/pull/7", "https://git.example:8443/o/r/pull/7", "https://git.example/o/r/pull/7/files", "https://git.example/o/r/pull/7/", "https://git.example/o/r/pull/",
                   "https://git.example/o/pull/7", "https://git.example/a/o/r/pull/7", "https://git.example/o/r/pulls/7", "https://git.example/o/r/pull/7 x", "https://git.example/o/r/pull/x7",
                   "https:///o/r/pull/7", "https://git_example/o/r/pull/7", "https://[::1]/o/r/pull/7", "https://[x/o/r/pull/7", "https://git.example:port/o/r/pull/7", "git.example/o/r/pull/7",
                   "file:/home/private/secret", "private/file.txt", "https://git.example/o%2Fr/r/pull/7", "https://git.example/../r/pull/7", "https://git.example/o/./pull/7", "https://git.example/o/r/pull/7\\x",
                   f"https://git.example/{worker(1)}/r/pull/7", f"https://git.example/o/{UUID_TEXT}/pull/7", "https://git.example/sess_Ab12Cd34Ef56/r/pull/7", "https://git.example/o/SESS_AB12CD34EF56/pull/7",
                   f"https://{UUID_TEXT}.example/o/r/pull/7")
        self.assertEqual([value for value in refused if link(value) != ("", "refused")], [])
