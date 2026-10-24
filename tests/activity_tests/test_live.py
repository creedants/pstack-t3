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
class LiveTest(OutputCase):
    def drained(self, process):
        ready, _, _ = select.select([process.stdout], [], [], 0)
        if not ready:
            return ""
        return process.stdout.readline().strip()

    def serve(self, *args, **how):
        """Start activity.py --serve 0 as a real interpreter and return its process and printed token address. A race: the server answers while this test fetches."""
        process = in_process.spawn(in_process.Reason.RACE, self.fixture.command("--serve", "0", *args, **how))

        def stop():
            process.terminate()
            process.wait(timeout=30)
            process.stdout.close()
            process.stderr.close()

        self.addCleanup(stop)
        deadline = time.time() + 30
        while time.time() < deadline:
            if process.poll() is not None:
                url = self.drained(process)
                if url.startswith("http://127.0.0.1:"):
                    return process, url
                try:
                    process.wait(timeout=max(0.0, deadline - time.time()))
                except subprocess.TimeoutExpired:
                    pass
                url = self.drained(process)
                if url.startswith("http://127.0.0.1:"):
                    return process, url
                if process.poll() is not None:
                    stderr = process.stderr.read()
                    self.fail(f"the live server exited with {process.returncode} before printing an address: {stderr.strip()[-500:]}")
                continue
            ready, _, _ = select.select([process.stdout], [], [], min(1.0, max(0.0, deadline - time.time())))
            if not ready:
                continue
            line = process.stdout.readline().strip()
            if not line:
                try:
                    process.wait(timeout=min(1.0, max(0.0, deadline - time.time())))
                except subprocess.TimeoutExpired:
                    pass
                continue
            self.assertTrue(line.startswith("http://127.0.0.1:"), line)
            return process, line
        if process.poll() is not None:
            stderr = process.stderr.read()
            self.fail(f"the live server exited with {process.returncode} before printing an address: {stderr.strip()[-500:]}")
        self.fail("the live server printed no address")

    def fetch(self, url, method=None, data=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method=method, data=data)) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def served(self):
        one_running(self.fixture).write()
        return self.serve()

    def test_serves_the_page_only_at_its_token_path(self):
        _, url = self.served()
        port = url.split(":")[2].split("/")[0]
        code, _, body = self.fetch(url)
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(b"<!doctype html>"), body[:80])
        code, _, body = self.fetch(url + "?x=1")
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(b"<!doctype html>"), body[:80])
        for target in (f"http://127.0.0.1:{port}/", url.rsplit("/", 1)[0] + "/wrong", url + "x"):
            with self.subTest(target=target):
                self.assertEqual(self.fetch(target)[0], 404)

    def test_get_page_get_missing_and_options_carry_null_cors_and_no_store(self):
        _, url = self.served()
        _, plain, _ = self.fetch(url)
        _, missing, _ = self.fetch(url.rsplit("/", 1)[0] + "/wrong")
        _, options, _ = self.fetch(url, method="OPTIONS")
        for headers in (plain, missing, options):
            with self.subTest(headers=dict(headers)):
                self.assertEqual(headers.get("Access-Control-Allow-Origin"), "null")
                self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_only_get_and_options_are_answered(self):
        _, url = self.served()
        self.assertEqual(self.fetch(url, data=b"x")[0], 501)
        self.assertEqual(self.fetch(url, method="OPTIONS")[0], 204)
        self.assertEqual(self.fetch(url)[0], 200)

    def test_the_server_binds_127_0_0_1_only(self):
        fixture = one_running(self.fixture).write()
        settings = MOD["Settings"](fixture.store, fixture.database, 3.0, MOD["BUDGET"])
        server = MOD["live_server"](settings, MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"]), 0)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "127.0.0.1")

    def test_the_server_binds_without_a_hostname_lookup(self):
        fixture = one_running(self.fixture).write()
        settings = MOD["Settings"](fixture.store, fixture.database, 3.0, MOD["BUDGET"])
        with mock.patch("socket.getfqdn", side_effect=AssertionError("no hostname lookup")):
            server = MOD["live_server"](settings, MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"]), 0)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_name, "127.0.0.1")

    def test_exit_before_output_reports_exit_status_and_stderr(self):
        script = "import sys; sys.stderr.write('probe error\\n'); sys.stderr.flush(); sys.exit(7)"
        proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        with mock.patch.object(in_process, "spawn", return_value=proc):
            with self.assertRaises(AssertionError) as ctx:
                self.serve()
        self.assertIn("the live server exited with 7 before printing an address: probe error", str(ctx.exception))

    def test_buffered_address_is_kept_when_server_has_exited(self):
        url = "http://127.0.0.1:1/activity/token"
        proc = subprocess.Popen([sys.executable, "-c", f"print({url!r}, flush=True)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        proc.wait(timeout=30)
        with mock.patch.object(in_process, "spawn", return_value=proc):
            got_proc, got_url = self.serve()
        self.assertIs(got_proc, proc)
        self.assertEqual(got_url, url)

    def test_the_token_file_is_mode_0600_and_reused_across_restarts(self):
        fixture = one_running(self.fixture).write()
        first = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        path = fixture.store / "activity-token"
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertGreaterEqual(len(first.token) * 8, 128)
        second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertEqual((second.token, second.route, second.url), (first.token, first.route, first.url))
        os.chmod(path, 0o644)
        third = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertNotEqual(third.token, first.token)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))

    def test_a_token_file_with_whitespace_around_the_token_is_reused(self):
        fixture = one_running(self.fixture).write()
        path = fixture.store / "activity-token"
        token = "t" * 43
        path.write_text(f" {token}\n\t")
        os.chmod(path, 0o600)
        second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertEqual(second.token, token.encode("ascii"))

    def test_short_token_is_replaced_with_a_private_regular_file(self):
        fixture = one_running(self.fixture).write()
        path = fixture.store / "activity-token"
        path.write_text("x")
        os.chmod(path, 0o600)
        second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertNotEqual(second.token, b"x")
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]{43}", second.token.decode("ascii")))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))

    def test_exposed_token_file_is_replaced_not_reused(self):
        fixture = one_running(self.fixture).write()
        first = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        path = fixture.store / "activity-token"
        os.chmod(path, 0o644)
        bad = path.read_text().strip()
        self.assertEqual(bad, first.token.decode("ascii"))
        second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertNotEqual(second.token.decode("ascii"), bad)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))

    def test_symlink_token_path_is_replaced_without_touching_its_target(self):
        fixture = one_running(self.fixture).write()
        path = fixture.store / "activity-token"
        target = Path(self.fixture.root) / "symlink-target"
        target.write_text("known")
        os.chmod(target, 0o644)
        before = (target.read_bytes(), stat.S_IMODE(os.stat(target).st_mode))
        os.symlink(target, path)
        second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertNotEqual(second.token, b"known")
        self.assertFalse(path.is_symlink())
        self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual((target.read_bytes(), stat.S_IMODE(os.stat(target).st_mode)), before)

    def test_invalid_utf8_token_file_is_replaced_with_a_private_regular_file(self):
        fixture = one_running(self.fixture).write()
        path = fixture.store / "activity-token"
        path.write_bytes(b"\xff\xfe\x00bad")
        os.chmod(path, 0o644)
        real_chmod = os.chmod

        def check_private_before_chmod(target, mode):
            if Path(target) == path and stat.S_IMODE(os.stat(path).st_mode) != 0o600:
                self.assertEqual(path.read_bytes(), b"\xff\xfe\x00bad", "a fresh token was readable before chmod")
            return real_chmod(target, mode)

        with mock.patch.object(os, "chmod", side_effect=check_private_before_chmod):
            second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]{43}", second.token.decode("ascii")))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))

    def test_empty_token_file_is_replaced_with_a_private_regular_file(self):
        fixture = one_running(self.fixture).write()
        path = fixture.store / "activity-token"
        path.write_text("")
        os.chmod(path, 0o644)
        real_chmod = os.chmod

        def check_private_before_chmod(target, mode):
            if Path(target) == path and stat.S_IMODE(os.stat(path).st_mode) != 0o600:
                self.assertEqual(path.read_bytes(), b"", "a fresh token was readable before chmod")
            return real_chmod(target, mode)

        with mock.patch.object(os, "chmod", side_effect=check_private_before_chmod):
            second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]{43}", second.token.decode("ascii")))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))

    def test_the_live_wrapper_holds_the_token_url_the_collapse_rule_and_both_buttons(self):
        fixture = one_running(self.fixture).write()
        wrapper = self.out("--live-page")
        token = (fixture.store / "activity-token").read_text().strip()
        self.assertIn(f"http://127.0.0.1:48731/activity/{token}", wrapper)
        self.assertIn("merged|dropped|done", wrapper)
        self.assertIn("Expand all", wrapper)
        self.assertIn("Collapse all", wrapper)
        self.assertIn("30000", wrapper)

    def test_live_page_with_https_origin_polls_that_origin(self):
        fixture = one_running(self.fixture).write()
        origin = "https://home.example.ts.net:48731"
        wrapper = self.out("--live-page", origin)
        token = (fixture.store / "activity-token").read_text().strip()
        self.assertIn(f"{origin}/activity/{token}", wrapper)
        self.assertNotIn("http://127.0.0.1:48731", wrapper)

    def test_live_page_with_path_query_or_non_http_scheme_is_refused_with_the_origin_rule(self):
        refused = "activity: --live-page takes an http:// or https:// origin with no userinfo, path, query, or fragment; pass one such as https://home.example.ts.net:48731, or omit it to use http://127.0.0.1:48731"
        for origin in ("https://home.example.ts.net:48731/activity", "https://home.example.ts.net:48731?x=1", "ftp://home.example.ts.net:48731"):
            with self.subTest(origin=origin):
                self.assertEqual(self.fails(1, "--live-page", origin), refused)

    def test_live_page_with_empty_port_is_refused_with_the_origin_rule(self):
        refused = "activity: --live-page takes an http:// or https:// origin with no userinfo, path, query, or fragment; pass one such as https://home.example.ts.net:48731, or omit it to use http://127.0.0.1:48731"
        self.assertEqual(self.fails(1, "--live-page", "https://home.example.ts.net:"), refused)

    def test_live_page_with_no_value_ignores_serve_and_still_names_48731(self):
        fixture = one_running(self.fixture).write()
        wrapper = self.out("--serve", "5000", "--live-page")
        token = (fixture.store / "activity-token").read_text().strip()
        self.assertIn(f"http://127.0.0.1:48731/activity/{token}", wrapper)
        self.assertNotIn("127.0.0.1:5000", wrapper)

    def test_live_page_help_says_the_no_value_address_does_not_follow_serve(self):
        parser = MOD["parser"]()
        live_page = next(action for action in parser._actions if action.dest == "live_page")
        self.assertIn("which does not follow --serve.", parser.description)
        self.assertIn("and does not follow --serve,", live_page.help)

    def test_a_broken_store_answers_503_with_the_one_line_message_and_no_page(self):
        fixture = one_running(self.fixture).write()
        (fixture.store / "restaurant.json").unlink()
        _, url = self.serve()
        for _ in range(2):
            code, _, body = self.fetch(url)
            self.assertEqual(code, 503)
            self.assertEqual(body, b"activity: that directory holds no coordinator's store; pass --at <store directory> or set BRIGADE_DIR")
