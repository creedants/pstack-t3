"""Draw four activity pages in a headless browser at 390 pixels wide and measure the page root element."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from dataclasses import replace
from pathlib import Path

try:
    from tests import test_activity
except ImportError:
    import test_activity

Fixture = test_activity.Fixture
populate = test_activity.populate
CLEAN = test_activity.CLEAN
MOD = test_activity.MOD

BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")
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


def confined_run(browser, target, directory, prefix):
    """Run browser on target with a profile under directory and browser state confined there."""
    environment = dict(os.environ)
    for variable, name in (("HOME", "h"), ("XDG_CONFIG_HOME", "c"), ("XDG_CACHE_HOME", "a"), ("XDG_STATE_HOME", "s"), ("TMPDIR", "t")):
        path = Path(directory) / name
        path.mkdir(parents=True, exist_ok=True)
        environment[variable] = str(path)
    profile = tempfile.mkdtemp(prefix=prefix, dir=directory)
    return subprocess.run([browser, "--headless", "--disable-gpu", "--no-sandbox", f"--user-data-dir={profile}", "--window-size=390,2000", "--dump-dom", str(target)],
                          capture_output=True, text=True, timeout=120, check=True, env=environment).stdout


def select_browser(directory):
    """The first browser of BROWSERS that starts on a small valid document under directory."""
    unavailable = []
    target = Path(directory) / "probe.html"
    target.write_text(f"<div id=o></div>{PINNED}{LOAD}")
    for name in BROWSERS:
        found = shutil.which(name)
        if found is None:
            unavailable.append(f"{name} not on PATH")
            continue
        try:
            confined_run(found, target, directory, "q")
        except subprocess.CalledProcessError as failed:
            unavailable.append(f"{found} exited {failed.returncode}")
        except subprocess.TimeoutExpired:
            unavailable.append(f"{found} timed out")
        except OSError as failed:
            unavailable.append(f"{found} failed to start with {failed}")
        else:
            return found, unavailable
    return None, unavailable


def measure(browser, document, directory):
    """The height in CSS pixels of the #o element with browser state confined under directory."""
    target = Path(directory) / "page.html"
    target.write_text(document.replace("<div id=o></div>", f"<div id=o></div>{PINNED}") + LOAD)
    drawn = confined_run(browser, target, directory, "u")
    return float(TITLE.search(drawn).group(1))


class RenderHeightTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.browser, unavailable = select_browser(self.root)
        if self.browser is None:
            self.skipTest(f"no working headless browser: {'; '.join(unavailable)}")

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


class BrowserSelectionTest(unittest.TestCase):
    def test_broken_browser_on_path_leaves_no_working_browser_and_names_its_exit(self):
        with tempfile.TemporaryDirectory() as bin_dir, tempfile.TemporaryDirectory() as root:
            stub = Path(bin_dir) / "chromium"
            stub.write_text("#!/bin/sh\nprintf 'browser cannot start\n' >&2\nexit 127\n")
            stub.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": bin_dir}):
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
