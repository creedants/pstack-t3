"""Draw four activity pages in a headless browser at 390 pixels wide and measure the page root element."""

import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from dataclasses import dataclass, replace
from pathlib import Path

try:
    from tests import in_process
except ImportError:
    import in_process

try:
    from tests import test_activity
except ImportError:
    import test_activity

Fixture = test_activity.Fixture
populate = test_activity.populate
CLEAN = test_activity.CLEAN
MOD = test_activity.MOD

BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")
# macOS app bundle executables, listed after the PATH names.
BUNDLED = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
# The platform axis of headless_flags.
MACOS = sys.platform == "darwin"
# Chrome on the macOS runner writes this display warning to stderr. The failure text drops these lines.
DISPLAY_WARNING = "CVDisplayLinkCreateWithCGDisplay failed"
# How many stderr lines a failure keeps, taken from the end.
STDERR_TAIL = 5
PROBE_TIMEOUT = 20
RENDER_TIMEOUT = 120
SOCKET_LIMIT = 107
BUDGET = 15999
# The frame fit() reserves.
FRAME = MOD["FRAME_HEIGHT"] - MOD["FRAME_MARGIN"]
# Headless Chromium floors the window width at 500 pixels, so the document pins the page to 390 pixels wide.
PINNED = "<style>html,body{margin:0}#o{width:390px}</style>"
LOAD = "<script>document.title=document.getElementById('o').getBoundingClientRect().height</script>"
TITLE = re.compile(r"<title>(.*?)</title>")


def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
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


def row(label, depth=0, status="done", spans=((0, 10, "done"),), open_seconds=None, stands_for=1, model="model-a"):
    bars = tuple(MOD["Span"](x, w, MOD["Status"](kind)) for x, w, kind in spans)
    return MOD["Row"](depth, label, model, "Claude", MOD["Status"](status), 60, bars, open_seconds, stands_for)


def item(ident, in_flight=True, summary="summary", pr="", no_link=""):
    return MOD["Item"](ident, summary, "working" if in_flight else "merged", "go" if in_flight else "", pr, in_flight, no_link)


def group(of, *rows):
    return MOD["Group"](of, tuple(rows), len(rows), 0)


def page(*groups, items=(), coordinator=None, hidden=None, name="kit"):
    rows = [each for one in groups for each in one.rows]
    totals = MOD["Totals"](sum(each.open_seconds is not None for each in rows), sum(each.stands_for for each in rows), 0, 0)
    return MOD["Page"](name, MOD["Window"](0.0, 10800.0), totals, (("Claude", totals.agents),), coordinator, tuple(groups), tuple(items), hidden or MOD["Hidden"]())


def give_kinds_and_goals(directory):
    """Give each work item row of the store's work item table the kind and goal cells it lacks, in the table's order."""
    path = directory / "dishes.tsv"
    header, *rows = path.read_text().splitlines()
    path.write_text("\n".join([header, *(f"{each}\tfeature\tG{number}" for number, each in enumerate(rows, start=1))]) + "\n")


def write_goals(directory, names):
    """Write the store's goals table, one goal per name, with the id G1 up."""
    columns = MOD["STORE_COLUMNS"]["goals.tsv"]
    lines = ["\t".join(columns)]
    for number, name in enumerate(names, start=1):
        goal = dict.fromkeys(columns, "")
        goal.update(id=f"G{number}", state="open", name=name)
        lines.append("\t".join(goal[column] for column in columns))
    (directory / "goals.tsv").write_text("\n".join(lines) + "\n")


def busy_page(directory, goal_names):
    """Build the page of a store of 36 work items and 400 agents, marking each row a feature of goal G<n> by row number and writing goal_names as the goals table."""
    fixture = Fixture(directory)
    populate(fixture, units=36, agents=400, running=5, failed=3)
    fixture.write()
    give_kinds_and_goals(fixture.store)
    write_goals(fixture.store, goal_names)
    store, t3, window = fixture.read()
    return MOD["build_page"](store, t3, window, MOD["privacy_of"](store, t3))


def wide_page(goal_names):
    """Build a page of eight groups of two rows, with goal G<n> and item D<n> numbered 1 up, named from goal_names."""
    goals = tuple(MOD["Goal"](f"G{number}", name) for number, name in enumerate(goal_names, start=1))
    items = tuple(replace(item(f"D{number}"), kind="feature", goal=f"G{number}") for number in range(1, 9))
    groups = tuple(group(items[number - 1], row(f"w{number}a"), row(f"w{number}b", depth=1)) for number in range(1, 9))
    return replace(page(*groups, items=items, coordinator=row("coordinator")), goals=goals, flight=MOD["flight_of"](items, goals))


@dataclass(frozen=True)
class BrowserMiss:
    name: str


@dataclass
class BrowserBinary:
    which_path: str
    names: list
    failure: str | None = None


def headless_flags(macos):
    """The flags the probe passes before the profile and target.

    macOS returns --headless with --no-first-run, --no-default-browser-check, --use-mock-keychain, and --disable-features=MediaRouter.
    Every other platform returns --headless with --disable-gpu and --no-sandbox.
    """
    if macos:
        return ["--headless", "--no-first-run", "--no-default-browser-check", "--use-mock-keychain", "--disable-features=MediaRouter"]
    return ["--headless", "--disable-gpu", "--no-sandbox"]


def stderr_segment(failed):
    """A colon and up to the last five non-empty stderr lines that are not the CVDisplayLink display warning, or an empty string when the browser wrote no other line.

    A TimeoutExpired from subprocess.run carries its stderr as bytes, so a bytes value is decoded before the lines are read.
    """
    stderr = failed.stderr or ""
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    lines = [line.strip() for line in stderr.splitlines()
             if line.strip() and DISPLAY_WARNING not in line][-STDERR_TAIL:]
    return f": {' '.join(lines)}" if lines else ""


def confined_run(browser, target, directory, prefix, timeout):
    """Run browser on target with a timeout of timeout seconds, headless_flags(MACOS), a profile under directory, HOME and XDG state confined there, and TMPDIR under /tmp."""
    environment = dict(os.environ)
    for variable, name in (("HOME", "h"), ("XDG_CONFIG_HOME", "c"), ("XDG_CACHE_HOME", "a"), ("XDG_STATE_HOME", "s")):
        path = Path(directory) / name
        path.mkdir(parents=True, exist_ok=True)
        environment[variable] = str(path)
    short_tmp = tempfile.mkdtemp(dir="/tmp")
    try:
        socket_path = os.path.join(short_tmp, "org.chromium.Chromium.XXXXXX", "SingletonSocket")
        if len(socket_path) > SOCKET_LIMIT:
            raise unittest.SkipTest(f"chromium socket path too long ({len(socket_path)} > {SOCKET_LIMIT})")
        environment["TMPDIR"] = short_tmp
        profile = tempfile.mkdtemp(prefix=prefix, dir=directory)
        return subprocess.run([browser, *headless_flags(MACOS), f"--user-data-dir={profile}", "--window-size=390,2000", "--dump-dom", str(target)],
                              capture_output=True, text=True, timeout=timeout, check=True, env=environment).stdout
    finally:
        shutil.rmtree(short_tmp, ignore_errors=True)


def skip_segments(items):
    """One segment per missing name and per failed binary, after later aliases are on the record.

    A failed binary's segment names its failure. A nonzero exit also names up to the last five non-empty lines of the browser's stderr that are not the CVDisplayLink display warning, when it wrote any.
    """
    lines = []
    for item in items:
        if isinstance(item, BrowserMiss):
            lines.append(f"{item.name} not found")
        elif item.failure is not None:
            lines.append(f"{', '.join(item.names)} ({item.which_path}) {item.failure}")
    return lines


def candidates():
    """Each browser name with the result of shutil.which for it, then each app bundle path that is a file.

    A PATH name that resolves to nothing is yielded with None. A bundle path that is not a file is skipped, so the sequence is unchanged where the bundles are absent.
    """
    for name in BROWSERS:
        yield name, shutil.which(name)
    for path in BUNDLED:
        if os.path.isfile(path):
            yield os.path.basename(path), path


def select_browser(directory, timeout=PROBE_TIMEOUT):
    """The first candidate that starts on a small valid document under directory.

    A candidate is a PATH name with the path shutil.which resolves, or an app bundle path that is a file. os.path.realpath resolves each hit. Each distinct real path is probed at most once with a timeout of timeout seconds, except when the file name is snap or the resolved path is /usr/lib/snapd/snapd, and that path is keyed by its unresolved path.
    """
    items = []
    by_resolved = {}
    target = Path(directory) / "probe.html"
    target.write_text(f"<div id=o></div>{PINNED}{LOAD}")
    for label, found in candidates():
        if found is None:
            items.append(BrowserMiss(label))
            continue
        resolved = os.path.realpath(found)
        key = found if os.path.basename(resolved) == "snap" or resolved == "/usr/lib/snapd/snapd" else resolved
        binary = by_resolved.get(key)
        if binary is None:
            binary = BrowserBinary(found, [label])
            by_resolved[key] = binary
            items.append(binary)
            try:
                confined_run(found, target, directory, "q", timeout)
            except subprocess.CalledProcessError as failed:
                binary.failure = f"exited {failed.returncode}{stderr_segment(failed)}"
            except subprocess.TimeoutExpired as failed:
                binary.failure = f"timed out after {failed.timeout} seconds{stderr_segment(failed)}"
            except OSError as failed:
                binary.failure = f"failed to start with {failed}"
            else:
                return found, skip_segments(items)
        else:
            binary.names.append(label)
    return None, skip_segments(items)


def measure(browser, document, directory):
    """The height in CSS pixels of the #o element with browser state confined under directory."""
    target = Path(directory) / "page.html"
    target.write_text(document.replace("<div id=o></div>", f"<div id=o></div>{PINNED}") + LOAD)
    drawn = confined_run(browser, target, directory, "u", RENDER_TIMEOUT)
    return float(TITLE.search(drawn).group(1))


class RenderHeightTest(unittest.TestCase):
    def setUp(self):
        required = os.environ.get("CI") == "true" or MACOS
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.browser, unavailable = select_browser(self.root)
        if self.browser is None and required:
            self.browser, unavailable = select_browser(self.root)
        if self.browser is None:
            reason = f"no working headless browser: {'; '.join(unavailable)}"
            if required:
                self.fail(reason)
            self.skipTest(reason)

    def test_height_of_each_page_drawn_at_390_pixels_is_not_above_the_height_the_model_reserves_or_the_frame(self):
        # The four pages the reviewer of pull request 192 measured, each with the reserved height, the timeline rows, and the flight groups the fit leaves on it.
        cases = (
            ("busy ordinary", busy_page(self.root / "ordinary", [f"Ship {number}" for number in range(1, 37)]), 1956, 11, 8),
            ("busy wide", busy_page(self.root / "wide", ["W" * 36] * 36), 1918, 8, 8),
            ("new short", wide_page([f"Ship {number}" for number in range(1, 9)]), 1886, 16, 8),
            ("new wide", wide_page(["W" * 36] * 8), 1890, 10, 8),
        )
        for name, unfolded, reserved, rows, groups in cases:
            with self.subTest(name):
                fitted, _ = MOD["fit"](unfolded, BUDGET, CLEAN, FRAME)
                drawn = [each for one in fitted.groups for each in one.rows]
                self.assertEqual(MOD["height"](fitted), reserved)
                self.assertEqual((len(drawn), len(fitted.flight.groups)), (rows, groups))
                height = measure(self.browser, MOD["render_html"](fitted, CLEAN), self.root)
                self.assertLessEqual(height, reserved)
                self.assertLessEqual(height, MOD["FRAME_HEIGHT"])


class LiveAttemptFoldTest(unittest.TestCase):
    def setUp(self):
        required = os.environ.get("CI") == "true" or MACOS
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.browser, unavailable = select_browser(self.root)
        if self.browser is None and required:
            self.browser, unavailable = select_browser(self.root)
        if self.browser is None:
            reason = f"no working headless browser: {'; '.join(unavailable)}"
            if required:
                self.fail(reason)
            self.skipTest(reason)

    def document_with_earlier_fold(self):
        fixture = Fixture(str(self.root / "live"))
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=test_activity.worker(3))
        fixture.retired("D7", test_activity.worker(1))
        fixture.retired("D7", test_activity.worker(2))
        fixture.thread(test_activity.worker(3), title="D7 worker", turns=(("running", 4.5, None),))
        fixture.thread(test_activity.worker(1), turns=(("completed", 90, 80),))
        fixture.thread(test_activity.worker(2), turns=(("completed", 70, 60),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        test_activity.write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": test_activity.worker(1), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "worker", "thread": test_activity.worker(2), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "3", "role": "worker", "thread": test_activity.worker(3), "start": stamp, "end": ""},
        ])
        fixture.write_database()
        result = in_process.invoke(fixture.command())
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        return result.stdout

    def test_live_fold_script_on_the_static_document_shows_earlier_rows_when_attempt_details_open_and_hides_them_on_close(self):
        probe = "".join((
            "<script>", MOD["LIVE_FOLD"],
            "fold();",
            "var details = document.querySelector('#o .tl .ldg details');",
            "if (!details) { document.title = 'no attempt details'; } else {",
            "var rows = Array.prototype.slice.call(document.querySelectorAll('#o .tl .row.earlier'));",
            "var disp = function () { return rows.map(function (r) { return getComputedStyle(r).display; }); };",
            "var before = disp();",
            "details.open = true;",
            "details.dispatchEvent(new Event('toggle'));",
            "var after = disp();",
            "details.open = false;",
            "details.dispatchEvent(new Event('toggle'));",
            "var shut = disp();",
            "document.title = JSON.stringify({before: before, after: after, shut: shut});",
            "}",
            "</script>",
        ))
        target = Path(self.root) / "live.html"
        target.write_text(self.document_with_earlier_fold().replace("<div id=o></div>", f"<div id=o></div>{PINNED}") + probe)
        drawn = confined_run(self.browser, target, self.root, "e", RENDER_TIMEOUT)
        seen = json.loads(html.unescape(TITLE.search(drawn).group(1)))
        self.assertEqual(seen, {"before": ["none", "none"], "after": ["grid", "grid"], "shut": ["none", "none"]})


class BrowserSelectionTest(unittest.TestCase):
    def test_broken_browser_on_path_leaves_no_working_browser_and_names_its_exit(self):
        with tempfile.TemporaryDirectory() as bin_dir, tempfile.TemporaryDirectory() as root:
            stub = Path(bin_dir) / "chromium"
            stub.write_text("#!/bin/sh\nprintf 'browser cannot start\n' >&2\nexit 127\n")
            stub.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": bin_dir}), mock.patch(f"{__name__}.BUNDLED", ()):
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
            with mock.patch.dict(os.environ, {"PATH": bin_dir}), mock.patch(f"{__name__}.BUNDLED", (str(stub),)):
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
            with mock.patch.dict(os.environ, {"PATH": bin_dir}), mock.patch(f"{__name__}.BUNDLED", (str(bundle),)):
                browser, _ = select_browser(root)
        self.assertEqual(browser, str(working))


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
            mock.patch(f"{__name__}.confined_run", side_effect=runner), \
            mock.patch(f"{__name__}.BUNDLED", bundled):
        return select_browser(root)


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
                mock.patch(f"{__name__}.confined_run", side_effect=runner):
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
                with mock.patch(f"{__name__}.MACOS", macos), mock.patch("subprocess.run", side_effect=fake_run):
                    confined_run("/opt/a", target, root, "q", 5)
                self.assertEqual(seen["args"][:len(flags) + 1], ["/opt/a", *flags])
                self.assertTrue(seen["args"][1 + len(flags)].startswith("--user-data-dir="))
                self.assertEqual(seen["args"][2 + len(flags):], ["--window-size=390,2000", "--dump-dom", str(target)])

    def test_probe_failure_keeps_the_last_five_stderr_lines_that_are_not_the_display_warning(self):
        warning = "[1:1:1:1:ERROR:ui/display/mac/cv_display_link_mac.mm:188] CVDisplayLinkCreateWithCGDisplay failed. CVReturn: -6670"
        cases = (
            (f"{warning}\nfirst\nsecond\nthird\n", "exited 2: first second third"),
            (f"one\ntwo\n{warning}\nthree\nfour\nfive\nsix\n", "exited 2: two three four five six"),
            (f"{warning}\n{warning}\n", "exited 2"),
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

        with tempfile.TemporaryDirectory() as root, mock.patch(f"{__name__}.confined_run", side_effect=runner):
            measure("/opt/a", "<div id=o></div>", root)
        self.assertEqual(timeouts, [RENDER_TIMEOUT])
        self.assertEqual(RENDER_TIMEOUT, 120)

    def test_success_returns_the_which_path(self):
        def runner(*args, **kwargs):
            return ""

        browser, _ = probe_with({"chromium": "/opt/link"}, {"/opt/link": "/snap/real"}, runner)
        self.assertEqual(browser, "/opt/link")

    def test_a_missed_probe_skips_once_locally_and_retries_and_fails_under_ci_or_on_macos(self):
        calls = []
        miss = lambda _directory: calls.append(os.environ.get("CI", "")) or (None, ["stub missed"])
        cases = (("", False, 1, False), ("true", False, 2, True), ("", True, 2, True))
        with mock.patch(f"{__name__}.select_browser", miss):
            for ci, macos, tries, failed in cases:
                with self.subTest(ci=ci, macos=macos), mock.patch.dict(os.environ, {"CI": ci}), mock.patch(f"{__name__}.MACOS", macos):
                    calls.clear()
                    result = unittest.TextTestRunner(stream=io.StringIO()).run(RenderHeightTest("test_height_of_each_page_drawn_at_390_pixels_is_not_above_the_height_the_model_reserves_or_the_frame"))
                    self.assertEqual((calls[:], "no working headless browser: stub missed" in (result.failures or result.skipped)[0][1], bool(result.failures), bool(result.skipped)), ([ci] * tries, True, failed, not failed))
