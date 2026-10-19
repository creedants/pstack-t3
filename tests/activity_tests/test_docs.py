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
class SkillTest(OutputCase):
    @unittest.skipUnless(shutil.which("bash"), "bash is not on PATH")
    def test_skills_command_lines_run_in_bash_with_a_space_in_the_skills_path_the_store_path_and_t3s_base_directory(self):
        fixture = one_running(self.fixture).write()
        skills, tools = fixture.root / "pathmarker skills", fixture.root / "tools"
        shutil.copytree(SCRIPT.parent,skills/"brigade"/"scripts")
        tools.mkdir()
        (tools / "python3").symlink_to(sys.executable)
        block = re.search(r"```bash\n(.*?)```", skill_section(), re.S).group(1)
        define, *calls = block.replace("<skills>", str(skills)).replace("<restaurant dir>", str(fixture.store)).splitlines()
        environment = {"HOME": str(fixture.home), "PATH": f"{tools}{os.pathsep}{os.environ.get('PATH', '')}", "T3CODE_HOME": str(fixture.base)}
        outputs = []
        for call in calls:
            result = subprocess.run(["bash", "-c", f"{define}\n{call}"], capture_output=True, text=True, env=environment, cwd=fixture.root)
            self.assertEqual((result.returncode, result.stderr), (0, ""), call)
            outputs.append(result.stdout)
        self.assertEqual([call.split("#")[0].split() for call in calls], [["A"], ["A", "--hours", "12"], ["A", "--text"]])
        self.assertEqual(([data_of(output)["w"][1] for output in outputs[:2]], outputs[2].split("\n")[0]), ([10800, 43200], "Agent activity for kit, last 3h"))

    def test_skill_sends_the_text_form_when_the_preview_fails_and_tells_the_coordinator_to_skip_no_step_of_visual_reports(self):
        section = skill_section()
        steps = dict(re.findall(r"^([0-9])\. (.*)$", section, re.M))
        self.assertEqual(steps["2"], "Call `html_preview` with that document. When the preview shows a console error or a clipped or overlapping element, "
                                     "send the stdout of `A --text` and name the fault in the reply. Render only a page whose preview passes. "
                                     "Call `html_render` with the document unchanged and the title `Agent activity`, "
                                     "per steps 3 to 4 of [Visual reports](../pstack-runtime/SKILL.md#visual-reports).")
        self.assertEqual([word for word in ("skip", "step 2 of") if word in section], [])
class ReferenceTest(unittest.TestCase):
    """The sentences the review of round 2 corrected, as the script's docstring and its generated reference now state them."""

    def test_reference_states_the_filter_and_the_pull_request_rule_as_the_docstring_does_and_holds_no_sentence_of_the_filter_it_replaced(self):
        reference = " ".join((ROOT / "docs/cli/activity.md").read_text().split())
        stated = ("Every string a page emits passes one filter after its parts are joined.",
                  "An identifier that reads as ordinary words is not private text.",
                  "The filter removes a word for no other reason.",
                  "Such a path starts with a slash before a character other than a space, a slash, a quotation mark, or a backtick, and it holds a second slash with no space, quotation mark, or backtick before it and a character other than those after it.",
                  "It leaves a word of one segment that starts with a slash, such as /review, unless it holds private text.",
                  "Userinfo, a query, and a fragment are dropped.",
                  "A work item on the page whose nonblank pull request value cannot be converted to such an address, or whose address the filter would change, has no link and is counted in a note.",
                  "The output and the newline after it take at most --max-bytes bytes.")
        self.assertEqual([sentence for sentence in stated if sentence not in reference or sentence not in " ".join(MOD["__doc__"].split())], [])
        replaced = ("4 characters or longer", "with any other value that is not blank", "the class Privacy", "6 or more digits", "the most bytes the HTML document",
                    "Such a path starts with a slash before a letter", "unless it holds a path this run read.")
        self.assertEqual([words for words in replaced if words in reference], [])
        self.assertIn("| `--max-bytes MAX_BYTES` | | | `16000` | the most bytes the output and the newline after it take, from 16000 to 500000 |", reference)
