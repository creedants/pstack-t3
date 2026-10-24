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
from tests.activity_tests import browser as browser_module
from tests.activity_tests.browser import BROWSERS, PROBE_TIMEOUT, RENDER_TIMEOUT, SOCKET_LIMIT, confined_run, measure, select_browser
def probe_with(which_map, resolved, runner, bundled=()):
    """Call select_browser with stubbed which, realpath, and confined_run, and bundled as the app bundle paths. No process starts."""
    original = os.path.realpath

    def which(name):
        return which_map.get(name)

    def realpath(path):
        if path in resolved:
            return resolved[path]
        return original(path)

    with tempfile.TemporaryDirectory() as root, \
            mock.patch("shutil.which", side_effect=which), \
            mock.patch("os.path.realpath", side_effect=realpath), \
            mock.patch(f"{browser_module.__name__}.confined_run", side_effect=runner), \
            mock.patch(f"{browser_module.__name__}.BUNDLED", bundled):
        return select_browser(root)

class BrowserSelectionTest(unittest.TestCase):
    def test_broken_browser_on_path_leaves_no_working_browser_and_names_its_exit(self):
        with tempfile.TemporaryDirectory() as bin_dir, tempfile.TemporaryDirectory() as root:
            stub = Path(bin_dir) / "chromium"
            stub.write_text("#!/bin/sh\nprintf 'browser cannot start\n' >&2\nexit 127\n")
            stub.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": bin_dir}), mock.patch(f"{browser_module.__name__}.BUNDLED", ()):
                browser, unavailable = select_browser(root)
        self.assertIsNone(browser)
        self.assertEqual(len(unavailable), len(BROWSERS))
        self.assertTrue(any("exited 127" in each for each in unavailable))
    def test_working_candidate_is_used_after_a_broken_first_one(self):
        with tempfile.TemporaryDirectory() as bin_dir, tempfile.TemporaryDirectory() as root:
            broken = Path(bin_dir) / "chromium"
            broken.write_text("#!/bin/sh\nexit 127\n")
            broken.chmod(0o755)
            working = Path(bin_dir) / "chromium-browser"
            working.write_text("#!/bin/sh\nexit 0\n")
            working.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": bin_dir}):
                browser, _ = select_browser(root)
        self.assertEqual(browser, str(working))
    def test_an_existing_app_bundle_browser_is_used_when_no_path_name_starts(self):
        with tempfile.TemporaryDirectory() as bin_dir, tempfile.TemporaryDirectory() as root:
            stub = Path(root) / "Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
            stub.parent.mkdir(parents=True)
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": bin_dir}), mock.patch(f"{browser_module.__name__}.BUNDLED", (str(stub),)):
                browser, unavailable = select_browser(root)
        self.assertEqual(browser, str(stub))
        self.assertEqual(unavailable, [f"{name} not found" for name in BROWSERS])
    def test_a_path_name_is_used_before_an_existing_app_bundle_browser(self):
        with tempfile.TemporaryDirectory() as bin_dir, tempfile.TemporaryDirectory() as root:
            bundle = Path(root) / "Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
            bundle.parent.mkdir(parents=True)
            bundle.write_text("#!/bin/sh\nexit 0\n")
            bundle.chmod(0o755)
            working = Path(bin_dir) / "chromium"
            working.write_text("#!/bin/sh\nexit 0\n")
            working.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": bin_dir}), mock.patch(f"{browser_module.__name__}.BUNDLED", (str(bundle),)):
                browser, _ = select_browser(root)
        self.assertEqual(browser, str(working))
class ProbeContractTest(unittest.TestCase):
    def test_two_names_for_one_binary_are_probed_once(self):
        calls = []

        def runner(browser, *args, **kwargs):
            calls.append(browser)
            raise subprocess.CalledProcessError(127, browser)

        probe_with(
            {"chromium": "/opt/a", "chromium-browser": "/opt/b"},
            {"/opt/a": "/snap/one", "/opt/b": "/snap/one"},
            runner,
        )
        self.assertEqual(calls, ["/opt/a"])
    def test_a_working_second_command_on_the_snap_launcher_is_probed(self):
        for resolved in ("/usr/bin/snap", "/usr/lib/snapd/snapd"):
            with self.subTest(resolved):
                calls = []

                def runner(browser, *args, **kwargs):
                    calls.append(browser)
                    if browser == "/snap/bin/chrome":
                        return ""
                    raise subprocess.CalledProcessError(127, browser)

                browser, unavailable = probe_with(
                    {"chromium": "/snap/bin/chromium", "chrome": "/snap/bin/chrome"},
                    {"/snap/bin/chromium": resolved, "/snap/bin/chrome": resolved},
                    runner,
                )
                self.assertEqual(calls, ["/snap/bin/chromium", "/snap/bin/chrome"])
                self.assertEqual(browser, "/snap/bin/chrome")
                self.assertEqual(unavailable, [
                    "chromium (/snap/bin/chromium) exited 127",
                    "chromium-browser not found",
                    "google-chrome not found",
                    "google-chrome-stable not found",
                ])
    def test_probe_timeout_is_twenty_seconds(self):
        timeouts = []

        def runner(browser, target, directory, prefix, timeout):
            timeouts.append(timeout)
            return ""

        probe_with({"chromium": "/opt/a"}, {"/opt/a": "/snap/one"}, runner)
        self.assertEqual(timeouts, [PROBE_TIMEOUT])
        self.assertEqual(PROBE_TIMEOUT, 20)
    def test_select_browser_with_timeout_60_passes_60_to_the_probe(self):
        timeouts = []

        def runner(browser, target, directory, prefix, timeout):
            timeouts.append(timeout)
            return ""

        original = os.path.realpath

        def which(name):
            return "/opt/a" if name == "chromium" else None

        def realpath(path):
            return "/snap/one" if path == "/opt/a" else original(path)

        with tempfile.TemporaryDirectory() as root, \
                mock.patch("shutil.which", side_effect=which), \
                mock.patch("os.path.realpath", side_effect=realpath), \
                mock.patch(f"{browser_module.__name__}.confined_run", side_effect=runner):
            browser, _ = select_browser(root, timeout=60)
        self.assertEqual(browser, "/opt/a")
        self.assertEqual(timeouts, [60])
    def test_skip_reason_names_each_alias_and_the_exit(self):
        def runner(browser, *args, **kwargs):
            raise subprocess.CalledProcessError(127, browser)

        _, unavailable = probe_with(
            {"chromium": "/opt/a", "chromium-browser": "/opt/b"},
            {"/opt/a": "/snap/one", "/opt/b": "/snap/one"},
            runner,
        )
        self.assertEqual(unavailable, [
            "chromium, chromium-browser (/opt/a) exited 127",
            "google-chrome not found",
            "google-chrome-stable not found",
            "chrome not found",
        ])
    def test_timeout_skip_names_the_exception_timeout(self):
        def runner(browser, *args, **kwargs):
            raise subprocess.TimeoutExpired(browser, 7)

        _, unavailable = probe_with({"chromium": "/opt/a"}, {"/opt/a": "/snap/one"}, runner)
        self.assertEqual(unavailable[0], "chromium (/opt/a) timed out after 7 seconds")
    def test_confined_run_tmpdir_keeps_singleton_socket_path_within_107_characters(self):
        seen = {}

        def fake_run(args, **kwargs):
            seen["env"] = kwargs["env"]
            completed = mock.Mock()
            completed.stdout = ""
            return completed

        with tempfile.TemporaryDirectory() as root:
            deep = Path(root) / ("d" * 80)
            deep.mkdir()
            target = deep / "probe.html"
            target.write_text("<div id=o></div>")
            with mock.patch("subprocess.run", side_effect=fake_run):
                confined_run("/opt/a", target, str(deep), "q", 5)
        tmpdir = seen["env"]["TMPDIR"]
        socket_path = os.path.join(tmpdir, "org.chromium.Chromium.XXXXXX", "SingletonSocket")
        self.assertLessEqual(len(socket_path), SOCKET_LIMIT)
        self.assertEqual(SOCKET_LIMIT, 107)
    def test_macos_probe_uses_the_macos_flags_and_other_platforms_keep_the_shared_three(self):
        cases = (
            (True, ["--headless", "--no-first-run", "--no-default-browser-check", "--use-mock-keychain", "--disable-features=MediaRouter"]),
            (False, ["--headless", "--disable-gpu", "--no-sandbox"]),
        )
        for macos, flags in cases:
            with self.subTest(macos), tempfile.TemporaryDirectory() as root:
                seen = {}

                def fake_run(args, **kwargs):
                    seen["args"] = args
                    completed = mock.Mock()
                    completed.stdout = ""
                    return completed

                target = Path(root) / "probe.html"
                target.write_text("<div id=o></div>")
                with mock.patch(f"{browser_module.__name__}.MACOS", macos), mock.patch("subprocess.run", side_effect=fake_run):
                    confined_run("/opt/a", target, root, "q", 5)
                self.assertEqual(seen["args"][:len(flags) + 1], ["/opt/a", *flags])
                self.assertTrue(seen["args"][1 + len(flags)].startswith("--user-data-dir="))
                self.assertEqual(seen["args"][2 + len(flags):], ["--window-size=390,2000", "--dump-dom", str(target)])
    def test_probe_failure_keeps_the_last_five_stderr_diagnostics_and_drops_only_the_warning_sentence(self):
        warning = "[1:1:1:1:ERROR:ui/display/mac/cv_display_link_mac.mm:188] CVDisplayLinkCreateWithCGDisplay failed. CVReturn: -6670"
        cases = (
            (f"{warning}\nfirst\nsecond\nthird\n", "exited 2: first second third"),
            (f"one\ntwo\n{warning}\nthree\nfour\nfive\nsix\n", "exited 2: two three four five six"),
            (f"{warning}\n{warning}\n", "exited 2"),
            (f"{warning} renderer exited\n", "exited 2: renderer exited"),
            (f"one\ntwo\nthree\nfour\nfive\n{warning} six\n", "exited 2: two three four five six"),
        )
        for stderr, expected in cases:
            with self.subTest(stderr):

                def runner(browser, *args, **kwargs):
                    raise subprocess.CalledProcessError(2, browser, stderr=stderr)

                _, unavailable = probe_with({"chromium": "/opt/a"}, {"/opt/a": "/snap/one"}, runner)
                self.assertEqual(unavailable[0], f"chromium (/opt/a) {expected}")
    def test_timeout_failure_decodes_the_bytes_stderr_a_real_timeout_carries(self):
        warning = b"[1:1:1:1:ERROR:ui/display/mac/cv_display_link_mac.mm:188] CVDisplayLinkCreateWithCGDisplay failed. CVReturn: -6670\n"
        cases = (
            (warning + b"[2:2:2:2:ERROR:startup.cc(9)] startup stalled\n", "timed out after 7 seconds: [2:2:2:2:ERROR:startup.cc(9)] startup stalled"),
            (warning.decode() + "[3:3:3:3:ERROR:startup.cc(9)] startup stalled\n", "timed out after 7 seconds: [3:3:3:3:ERROR:startup.cc(9)] startup stalled"),
            (warning, "timed out after 7 seconds"),
            (warning.strip() + b" renderer exited\n", "timed out after 7 seconds: renderer exited"),
        )
        for stderr, expected in cases:
            with self.subTest(stderr):

                def runner(browser, *args, **kwargs):
                    raise subprocess.TimeoutExpired(browser, 7, stderr=stderr)

                _, unavailable = probe_with({"chromium": "/opt/a"}, {"/opt/a": "/snap/one"}, runner)
                self.assertEqual(unavailable[0], f"chromium (/opt/a) {expected}")
    def test_render_keeps_its_own_timeout(self):
        timeouts = []

        def runner(browser, target, directory, prefix, timeout):
            timeouts.append(timeout)
            return "<title>10</title>"

        with tempfile.TemporaryDirectory() as root, mock.patch(f"{browser_module.__name__}.confined_run", side_effect=runner):
            measure("/opt/a", "<div id=o></div>", root)
        self.assertEqual(timeouts, [RENDER_TIMEOUT])
        self.assertEqual(RENDER_TIMEOUT, 120)
    def test_success_returns_the_which_path(self):
        def runner(*args, **kwargs):
            return ""

        browser, _ = probe_with({"chromium": "/opt/link"}, {"/opt/link": "/snap/real"}, runner)
        self.assertEqual(browser, "/opt/link")
