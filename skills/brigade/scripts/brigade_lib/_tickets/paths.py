import fnmatch
import posixpath
import re
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath
from brigade_lib._tickets.rail import base_source
from brigade_lib.shared import BrigadeError
from brigade_lib.store import GENERATED_ROOT, LINE_SUFFIX, SOURCE_PRIORITY, SOURCE_ROOT

def priority_of(row):
    return row["priority"] or SOURCE_PRIORITY.get(base_source(row["source"]), "normal")


def paths_of(row):
    return tuple(part for part in row["paths"].split(",") if part)


def ticket_paths(text):
    if text == "":
        return ()
    found = set()
    for part in text.split(","):
        path = posixpath.normpath(part.strip().replace("\\", "/")).lstrip("/")
        if path in ("", ".", "..") or path.startswith("../"):
            raise BrigadeError(f"--paths takes files or directories inside the repository, got {part!r}")
        found.add(path)
    return tuple(sorted(found))


@dataclass(frozen=True)
class Tracked:
    files: frozenset
    directories: frozenset
    names: dict


def unique_names(files):
    sources = {path.rsplit("/", 1)[-1] for path in files if path.startswith(SOURCE_ROOT)}
    by_name = {}
    for path in files:
        name = path.rsplit("/", 1)[-1]
        if not (path.startswith(GENERATED_ROOT) and name in sources):
            by_name.setdefault(name, []).append(path)
    return {name: paths[0] for name, paths in by_name.items() if len(paths) == 1}


def tracked_paths(project_root):
    try:
        result = subprocess.run(["git", "-C", str(project_root), "ls-files", "-z"], capture_output=True,
                                encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None
    if result.returncode != 0:
        return None
    files = frozenset(name for name in result.stdout.split("\0") if name)
    directories = frozenset(str(above) for name in files for above in PurePosixPath(name).parents if str(above) != ".")
    return Tracked(files, directories, unique_names(files))


def pattern_directory(parts):
    marked = next(index for index, part in enumerate(parts) if re.search(r"[*?\[]", part))
    return "/".join(parts[:marked])


def quoted_paths(text, tracked):
    if tracked is None:
        return ()
    found = set()
    for quote in re.finditer(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", text):
        content = quote.group(2)
        span = re.sub(LINE_SUFFIX, "", content.strip())
        if not span or re.search(r"\s", span) or span.startswith("/") or ".." in span.split("/"):
            continue
        span = posixpath.normpath(span)
        if not re.search(r"[*?\[]", span):
            if span != "." and (span in tracked.files or span in tracked.directories):
                found.add(span)
            elif content not in tracked.files and content not in tracked.directories:
                name = re.sub(LINE_SUFFIX, "", content)
                if not re.search(r"[/\s*?\[]", name) and name in tracked.names:
                    found.add(tracked.names[name])
            continue
        directory = pattern_directory(span.split("/"))
        if directory and fnmatch.filter(tracked.files, span):
            found.add(directory)
    return tuple(sorted(found))


def record_paths(restaurant, tracked, write):
    rows = restaurant.rows("rail.tsv")
    lines, recorded = [], False
    for row in rows:
        if row["state"] != "waiting" or paths_of(row):
            continue
        paths = ",".join(quoted_paths(row["summary"], tracked))
        if paths:
            lines.append(f"{row['id']} {'paths' if write else 'would record'} {paths}")
            row["paths"], recorded = paths, True
        else:
            lines.append(f"{row['id']} quotes no tracked path")
    if write and recorded:
        restaurant.save_rows("rail.tsv", rows)
    return "\n".join(lines) or "every waiting ticket records paths"
