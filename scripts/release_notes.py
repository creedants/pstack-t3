#!/usr/bin/env python3
"""Judge the release notes file a release passes with --notes-file."""

import re
import sys
from pathlib import Path

STABLE = re.compile(r"Stable check: T3 Code (\d+\.\d+\.\d+) (passed|failed)")
SECTIONS = ("## New", "## Fixed", "## Before you upgrade")


def text(path):
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def stable(notes):
    for line in notes.splitlines():
        match = STABLE.match(line.strip().removeprefix("- "))
        if match:
            if match.group(2) != "passed":
                sys.exit(f"stable check on T3 Code {match.group(1)} failed; record a passing result")
            return
    sys.exit("no stable check record; run setup-pstack and one small real item on the latest stable T3 Code release, "
        "record `Stable check: T3 Code 1.2.3 passed` in the release notes file, with 1.2.3 the version you ran, and pass it with --notes-file")


def section_gap(notes):
    lines = notes.splitlines()
    missing = []
    for heading in SECTIONS:
        at = next((index for index, line in enumerate(lines) if line.rstrip(" ") == heading), None)
        if at is None:
            missing.append(heading)
            continue
        body = []
        for line in lines[at + 1:]:
            if re.match(r"#{1,2}(?:\s|$)", line):
                break
            body.append(line)
        if not any(line.strip(" \t") for line in body):
            missing.append(heading)
    if not missing:
        return None
    return f"release notes file lacks {', '.join(missing)}; add each heading with a non-empty line under it"
