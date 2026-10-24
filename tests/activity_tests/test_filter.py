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
class FilterTest(FilterCase):
    def test_clean_removes_each_word_that_holds_an_identifier_it_was_given_in_any_case_as_written_and_percent_encoded_once_and_twice(self):
        child = delegated(COORDINATOR, "kit-d7-audit")
        ids = {child, COORDINATOR, "sess_Ab12Cd34Ef56", "x y_secret1 z"}
        for value in (child, unquote(child), unquote(unquote(child)), COORDINATOR, "sess_Ab12Cd34Ef56", "x y_secret1 z"):
            forms = (value, value.upper(), value.lower(), value.swapcase(), quote(value, safe=""), quote(quote(value, safe=""), safe=""), quote(value, safe="").lower(),
                     value.replace("-", "%2D").replace("_", "%5f"), quote(value))
            for form in forms:
                with self.subTest(form):
                    self.assertEqual((self.clean(f"a {form} b", ids), self.clean(f"a pre{form}post b", ids), self.clean(f"a (id={form}), b", ids)), ("a b", "a b", "a b"))
    def test_clean_keeps_an_identifier_that_reads_as_ordinary_words_and_every_ordinary_word(self):
        text = "Fix the queue code review in src for private auditor 451237 on D7 of pstack-t3 with gpt-6.1-sol and claude-sonnet-4-5-20250929 by kit-d7-audit-task at 0a1b2c3"
        self.assertEqual(self.clean(text, ORDINARY), text)
        self.assertEqual([value for value in ORDINARY if not MOD["ordinary"](value)], [])
        structured = (COORDINATOR, worker(1), native(1), "sess_Ab12Cd34Ef56", "0123456789abcdef0", "a" * 49, "two  spaces", "queue/report", "queue:report", "-queue", "queue-")
        self.assertEqual([value for value in structured if MOD["ordinary"](value)], [])
        self.assertEqual(self.clean("the 0123456789abcdef0 run and abc", {"0123456789abcdef0", "abc"}), "the run and abc")
    def test_clean_removes_each_word_that_holds_a_path_it_was_given_and_a_path_below_it(self):
        paths = {"/srv/pathmarker state/proj", "/home/homemarker"}
        self.assertEqual(self.clean("read /srv/pathmarker state/proj and /SRV/PATHMARKER STATE/PROJ/kit/x.md and x=/home/homemarker/.t3 end", paths=paths), "read and and end")
        self.assertEqual(self.clean("in %2Fsrv%2Fpathmarker%20state%2Fproj and %252Fhome%252Fhomemarker%252Fa and pre/home/homemarkerpost end", paths=paths), "in and and end")
    def test_clean_removes_a_word_that_holds_a_uuid_or_one_of_t3s_id_words_before_a_colon_and_a_letter_digit_underscore_or_percent_sign(self):
        self.assertEqual(self.clean(f"at {UUID_TEXT} and x{UUID_TEXT.upper()}y and {quote(UUID_TEXT)} end"), "at and and end")
        for word in MOD["T3_KINDS"]:
            with self.subTest(word):
                self.assertEqual(self.clean(f"a {word}:alpha b {word.upper()}:1 c prefix={word}:x d {word}%3Aalpha e {word}%253A_x f x{word}:%41 g {word}:%20x h"), "a b c d e f g h")
                self.assertEqual(self.clean(f"the {word}: one and {word} :x and {word}:) end"), f"the {word}: one and {word} :x and {word}:) end")
        self.assertEqual(self.clean("12:30 D7:fix feature:x key:value a::b Re:plan"), "12:30 D7:fix feature:x key:value a::b Re:plan")
    def test_clean_removes_a_word_that_holds_an_email_address_and_keeps_an_at_sign_with_no_address_around_it(self):
        self.assertEqual(self.clean("mail acct-homemarker@example.test and <a.b@c.org>, x=USER@EXAMPLE.TEST and user%40example.test now"), "mail and and now")
        self.assertEqual(self.clean("mail \"jd\"@example.test now"), "mail now")
        kept = "bump react@18.2.0 for @handle on user@host at a@b and @example.test and (jd)@example.test"
        self.assertEqual(self.clean(kept), kept)
    def test_clean_removes_the_listed_addresses_with_a_quoted_local_part_of_1_to_62_characters_or_a_bracketed_host_and_keeps_the_listed_neighbours(self):
        for word in ('"auditor"@example.test', "auditor@[127.0.0.1]", "auditor@[IPv6:2001:db8::1]", "AUDITOR@[ipv6:2001:DB8::1]", '"a.b"@[127.0.0.1]', "<auditor@[127.0.0.1]>", "%22auditor%22%40example.test", '"abc\\"@example.test'):
            with self.subTest(word):
                self.assertEqual(self.clean(f"mail {word} now"), "mail now")
        self.assertEqual(self.clean('mail "the auditor"@example.test now'), "mail now")
        long = f'"{"a" * 63}"@example.test'
        self.assertEqual((self.clean(f"mail {long[0]}{long[2:]} now"), self.clean(f"mail {long} now")), ("mail now", f"mail {long} now"))
        kept = "bump react@18.2.0 for @handle on user@host and (jd)@example.test with list[0]@[a] or x@[1] and x@[1.2.3] then x@[1234.1.1.1] and x@[IPv6:] or \"\"@example.test and \"jd\" @example.test now"
        self.assertEqual(self.clean(kept), kept)
    def test_clean_removes_the_listed_addresses_whose_quoted_local_part_of_1_to_62_characters_holds_a_backslash_and_the_character_after_it_as_a_pair_and_keeps_the_listed_neighbours(self):
        words = ('"audit\\""@example.test', '"a\\"b"@example.test', '"a\\"b\\"c"@example.test', '"\\""@example.test', '"a\\\\b"@example.test', '"ab\\\\"@example.test', '"ab\\\\\\""@example.test',
                 '"audit\\""@[127.0.0.1]', '"audit\\""@[IPv6:2001:db8::1]', '"AUDIT\\""@EXAMPLE.TEST', '<"audit\\""@example.test>', "%22audit%5C%22%22%40example.test", "%2522audit%255C%2522%2522%2540example.test",
                 '"a\\%0Ab\\""@example.test')
        for word in words:
            with self.subTest(word):
                self.assertEqual(self.clean(f"mail {word} now"), "mail now")
        self.assertEqual(self.clean('mail "the\\ audit\\" one"@example.test now'), "mail now")
        full, over = (f'"{"a" * count}\\""@example.test' for count in (60, 61))
        self.assertEqual((self.clean(f"mail {full} now"), self.clean(f"mail {over} now")), ("mail now", f"mail {over} now"))
        kept = 'see \\"@example.test and "ab\\\\""@example.test with "a\\"b" @example.test or "a\\"b"@host and \\" @example.test now'
        self.assertEqual(self.clean(kept), kept)
    def test_clean_removes_a_request_name_with_a_hyphen_as_a_whole_token_in_any_case_and_keeps_a_longer_token_the_token_after_a_slash_that_starts_a_word_and_a_name_with_no_hyphen(self):
        clean = MOD["Privacy"](requests={"kit-d7-audit-task", "code-review", "queue", "a-b"}).clean
        self.assertEqual(clean("see kit-d7-audit-task now and KIT-D7-AUDIT-TASK. or x.kit-d7-audit-task, with (code-review) then x_code-review and a-b end"), "see now and or with then and end")
        self.assertEqual(clean("id=kit%2Dd7%2Daudit%2Dtask and &quot;code-review&quot; and src/code-review end"), "and and end")
        kept = "Run /code-review today on the queue for kit-d7-audit-task-2 and prekit-d7-audit-taskpost with x=/code-review and (/code-review) or code review and pre-code-review of a-b-c"
        self.assertEqual(clean(kept), kept)
        self.assertEqual(MOD["Privacy"]().clean("see kit-d7-audit-task now"), "see kit-d7-audit-task now")
    def test_clean_removes_a_hyphenated_request_name_from_the_end_of_a_30000_character_value_of_m_and_spaces(self):
        text = ("m " * 14994) + " code-review"
        self.assertEqual(len(text), 30000)
        self.assertEqual(MOD["Privacy"](requests={"code-review"}).clean(text), ("m " * 14993) + "m")
    def test_clean_removes_a_percent_encoded_hyphenated_request_name_from_the_end_of_a_30000_character_value_of_m_and_spaces(self):
        text = ("m " * 14993) + " code%2Dreview"
        self.assertEqual(len(text), 30000)
        self.assertEqual(MOD["Privacy"](requests={"code-review"}).clean(text), ("m " * 14992) + "m")
    def test_found_yields_the_reviewed_request_name_spans(self):
        privacy = MOD["Privacy"](requests={"code-review"})
        self.assertEqual(privacy.clean("m" * 30000 + "-"), "m" * 30000 + "-")
        cases = (
            ("code-review", [(0, 11)]),
            ("/code-review", []),
            ("src/code-review", [(4, 15)]),
            ("x=/code-review", []),
            ("(/code-review)", []),
            ("pre-code-review", []),
            ("code-review--code-review", [(0, 11), (13, 24)]),
            ("code-review code-review", [(0, 11), (12, 23)]),
            ("code-review-", [(0, 11)]),
            ("-code-review", [(1, 12)]),
            ("/-code-review", [(2, 13)]),
            ("x_code-review", [(2, 13)]),
            ("x//code-review", [(3, 14)]),
            ("//code-review", [(2, 13)]),
            ("m" * 30000 + "-", []),
        )
        for text, spans in cases:
            with self.subTest(text[:24]):
                self.assertEqual(list(privacy.found(text)), spans)
    def test_clean_removes_a_request_name_with_a_hyphen_and_an_underscore_a_dot_or_a_doubled_hyphen_anywhere_in_a_word_when_it_is_4_or_more_characters(self):
        clean = MOD["Privacy"](requests={"odd_name-1", "v1.2-check", "two--parts"}).clean
        self.assertEqual(clean("see odd_name-1 and preODD_NAME-1post and v1.2-check-2 and odd%5Fname%2D1 and xtwo--partsx end"), "see and and and and end")
        self.assertEqual(clean("see odd_name and v1.2 check end"), "see odd_name and v1.2 check end")
    def test_clean_removes_a_request_name_of_fewer_than_4_characters_with_a_hyphen_that_is_not_letters_and_digits_joined_by_single_hyphens_only_where_no_letter_digit_underscore_or_hyphen_is_directly_before_or_after_it(self):
        clean = MOD["Privacy"](requests={"a-_", "A-."}).clean
        for word in ("a-_", "A-_", "(a-_)", '"a-_"', "x=a-_", "a-_,", "a-_.", "a-_:", "/a-_", "src/a-_", "a-_/x", "x.a-_", "a%2D_", "a%2D%5F", "a%252D_", "a-.", "a-..", "(A-.)", "a%2D%2E"):
            with self.subTest(word):
                self.assertEqual(clean(f"see {word} now"), "see now")
        kept = "see xa-_ and a-_b with a-__ or a-_-2 then pre-a-_ and _a-_ for a-.py at 1a-. and a- with a - _ and a_- now"
        self.assertEqual(clean(kept), kept)
        self.assertEqual(MOD["Privacy"]().clean("see a-_ and a-. now"), "see a-_ and a-. now")
    def test_clean_removes_a_request_name_that_is_one_hyphen_two_hyphens_or_a_letter_before_or_after_a_hyphen_only_where_no_letter_digit_underscore_or_hyphen_is_directly_before_or_after_it(self):
        cases = {"-": ("a - b and (-) or -. end", "a b and or end", "a well-known -5 with -- and -v or a- then a-b and _- now"),
                 "--": ("a -- b and (--) end", "a b and end", "a --flag with a--b or --- and - then a-b now"),
                 "x-": ("a x- b and X-. or (x-) end", "a b and or end", "a x-ray with x-- or ax- and x-_ then x - and x now"),
                 "-y": ("a -y b and -Y, or =-y end", "a b and or end", "a x-y with -yes or --y and _-y then - y and y now")}
        for name, (text, left, kept) in cases.items():
            with self.subTest(name):
                clean = MOD["Privacy"](requests={name}).clean
                self.assertEqual((clean(text), clean(kept)), (left, kept))
    def test_clean_reads_the_listed_request_names_without_the_spaces_that_start_and_end_them_and_removes_the_forms_of_fewer_than_4_characters_that_their_percent_escapes_decode_to(self):
        self.assertEqual(MOD["Privacy"](requests={" a-_ "}).clean("see a-_"), "see")
        self.assertEqual(MOD["Privacy"](requests={" code-review "}).clean("run code-review and /code-review"), "run and /code-review")
        clean = MOD["Privacy"](requests={"%61-%5F", "b-%2520"}).clean
        self.assertEqual(clean("see a-_ and %61-%5F or A-_. then b- and b-%20 end"), "see and or then and end")
        self.assertEqual(clean("see xa-_ and b-b end"), "see xa-_ and b-b end")
