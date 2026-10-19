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
from dataclasses import dataclass, replace
from pathlib import Path
from unittest import mock

from tests.activity_tests.support import MOD, Fixture, populate, write_goal_names
from tests.activity_tests.pages import CLEAN, group, item, page, row

BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")
BUNDLED = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
MACOS = sys.platform == "darwin"
DISPLAY_WARNING = "CVDisplayLinkCreateWithCGDisplay failed"
_WARNING_PREFIX = re.compile(r"\[[^\]]*\]\s*")
_CVRETURN_CLAUSE = re.compile(r"^\s*\.\s*CVReturn:\s*-?\d+")
STDERR_TAIL = 5
PROBE_TIMEOUT = 20
RENDER_TIMEOUT = 120
SOCKET_LIMIT = 107
BUDGET = 15999
FRAME = MOD["FRAME_HEIGHT"] - MOD["FRAME_MARGIN"]
PINNED = "<style>html,body{margin:0}#o{width:390px}</style>"
LOAD = "<script>document.title=document.getElementById('o').getBoundingClientRect().height</script>"
TITLE = re.compile(r"<title>(.*?)</title>")
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
def _kept_text(line):
    """The stripped diagnostic of one stderr line, or an empty string when the line holds only the display warning sentence.

    The sentence is the warning phrase with its trailing CVReturn clause removed, and with a leading bracket prefix removed only when the prefix is the sole text before the phrase. Any other text on the line is kept.
    """
    text = line.strip()
    if not text:
        return ""
    index = text.find(DISPLAY_WARNING)
    if index < 0:
        return text
    before = text[:index]
    after = text[index + len(DISPLAY_WARNING):]
    if _WARNING_PREFIX.fullmatch(before):
        before = ""
    after = _CVRETURN_CLAUSE.sub("", after, count=1)
    return " ".join(part for part in (before.strip(), after.strip()) if part)

def stderr_segment(failed):
    """A colon and up to the last five kept stderr diagnostics, or an empty string when the browser wrote no other diagnostic.

    A TimeoutExpired from subprocess.run carries its stderr as bytes, so a bytes value is decoded before the lines are read. A line that holds only the display warning sentence keeps nothing. A line that holds other text with the warning keeps that text.
    """
    stderr = failed.stderr or ""
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    kept = [_kept_text(line) for line in stderr.splitlines()]
    lines = [line for line in kept if line][-STDERR_TAIL:]
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

    A failed binary's segment names its failure. A nonzero exit also names up to the last five kept diagnostics from the browser stderr, when it wrote any.
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

def give_kinds_and_goals(directory):
    """Give each work item row of the store's work item table the kind and goal cells it lacks, in the table's order."""
    path = directory / "dishes.tsv"
    header, *rows = path.read_text().splitlines()
    path.write_text("\n".join([header, *(f"{each}\tfeature\tG{number}" for number, each in enumerate(rows, start=1))]) + "\n")
def busy_page(directory, goal_names):
    """Build the page of a store of 36 work items and 400 agents, marking each row a feature of goal G<n> by row number and writing goal_names as the goals table."""
    fixture = Fixture(directory)
    populate(fixture, units=36, agents=400, running=5, failed=3)
    fixture.write()
    give_kinds_and_goals(fixture.store)
    write_goal_names(fixture.store, goal_names)
    store, t3, window = fixture.read()
    return MOD["build_page"](store, t3, window, MOD["privacy_of"](store, t3))
def wide_page(goal_names):
    """Build a page of eight groups of two rows, with goal G<n> and item D<n> numbered 1 up, named from goal_names."""
    goals = tuple(MOD["Goal"](f"G{number}", name) for number, name in enumerate(goal_names, start=1))
    items = tuple(replace(item(f"D{number}"), kind="feature", goal=f"G{number}") for number in range(1, 9))
    groups = tuple(group(items[number - 1], row(f"w{number}a"), row(f"w{number}b", depth=1)) for number in range(1, 9))
    return replace(page(*groups, items=items, coordinator=row("coordinator")), goals=goals, flight=MOD["flight_of"](items, goals))
