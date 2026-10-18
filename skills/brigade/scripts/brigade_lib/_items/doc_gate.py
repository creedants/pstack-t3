import os
import posixpath
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from brigade_lib.compact import refuse_setting
from brigade_lib.tickets import tracked_paths

@dataclass(frozen=True)
class _SharedDoc:
    root: str
    kind: str


_CATALOG = ()


@dataclass(frozen=True)
class DocPolicy:
    docs: tuple[_SharedDoc, ...]
    minutes: int


def _repo_relative(value):
    """The repository-relative path one sharedDocs entry names, or None when it is not one."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("\\", "/")
    if text.startswith("/") or ".." in text.split("/") or re.search(r"\s", text):
        return None
    made = posixpath.normpath(text).lstrip("/")
    if made in ("", "."):
        return None
    return made


def doc_policy(meta):
    """The shared-document catalog and timebox the store records.

    A missing key keeps its default. A present value that does not parse is refused through
    refuse_setting, so the message names the file, the key, and the value. A file entry names
    one document, and a file entry whose path ends with a slash names a directory prefix, so
    files under it are documents. A skill entry names a directory whose SKILL.md files are
    documents.
    """
    raw = meta.get("sharedDocs", [])
    if not isinstance(raw, list):
        refuse_setting("sharedDocs", raw, "it is not a list")
    docs = list(_CATALOG)
    for entry in raw:
        if not isinstance(entry, dict):
            refuse_setting("sharedDocs", raw, f"an entry is not an object: {entry!r}")
        unknown = [key for key in entry if key not in ("path", "kind")]
        if unknown:
            refuse_setting("sharedDocs", raw, f"an entry has unknown key {unknown[0]!r}")
        given = entry.get("path")
        path = _repo_relative(given)
        if path is None:
            refuse_setting("sharedDocs", raw, f"an entry's 'path' is not a repository path: {given!r}")
        kind = entry.get("kind")
        if kind not in ("file", "skill"):
            refuse_setting("sharedDocs", raw, f"an entry's 'kind' is not file or skill: {kind!r}")
        directory = kind == "skill" or given.replace("\\", "/").endswith("/")
        docs.append(_SharedDoc(path + "/" if directory else path, kind))
    minutes = meta.get("docTimebox", 90)
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
        refuse_setting("docTimebox", minutes, "it is not a whole number of minutes above zero")
    return DocPolicy(tuple(docs), minutes)


def _fixed_docs(catalog):
    return tuple(doc.root for doc in catalog if doc.kind == "file" and not doc.root.endswith("/"))


def _prefix_docs(catalog):
    return tuple(doc.root for doc in catalog if doc.kind == "file" and doc.root.endswith("/"))


def _skill_roots(catalog):
    return tuple(doc.root for doc in catalog if doc.kind == "skill")


def _covers(lease, doc):
    return lease == "" or doc == lease or doc.startswith(lease + "/")


def _is_skill_file(doc, roots):
    return doc.endswith("/SKILL.md") and doc.startswith(roots)


def _lease_parts(paths):
    if not paths:
        return []
    found = []
    for raw in paths.split(","):
        if raw.strip() == "":
            if "" not in found:
                found.append("")
            continue
        value = posixpath.normpath(raw.strip().replace("\\", "/")).lstrip("/")
        if value in (".", ""):
            value = ""
        if value == ".." or value.startswith("../"):
            continue
        if value not in found:
            found.append(value)
    return found


def _can_cover_catalog(parts, catalog):
    fixed = _fixed_docs(catalog)
    prefixes = _prefix_docs(catalog)
    roots = _skill_roots(catalog)
    for part in parts:
        if part == "":
            return True
        for doc in fixed:
            if doc == part or doc.startswith(part + "/"):
                return True
        for doc in prefixes:
            stripped = doc.rstrip("/")
            if part == stripped or stripped.startswith(part + "/") or part.startswith(doc):
                return True
        for root in roots:
            stripped = root.rstrip("/")
            if part == stripped or stripped.startswith(part + "/") or part.startswith(stripped + "/"):
                return True
    return False


def _walk_opaque(directory, prefix):
    found = set()
    for current, directories, filenames in os.walk(directory, followlinks=False):
        directories[:] = [name for name in directories
                          if name != ".git" and not os.path.islink(os.path.join(current, name))]
        relative = os.path.relpath(current, directory)
        head = prefix if relative == os.curdir else f"{prefix}{relative.replace(os.sep, '/')}/"
        found.update(f"{head}{name}" for name in filenames if not os.path.islink(os.path.join(current, name)))
    return found


def _gate_inventory(project_root):
    try:
        result = subprocess.run(["git", "-C", str(project_root), "ls-files", "--cached", "--others", "-z"],
                                capture_output=True, encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None
    if result.returncode != 0:
        return None
    names = set(name for name in result.stdout.split("\0") if name)
    root = Path(str(project_root))
    for name in [name for name in names if name.endswith("/")]:
        names.update(_walk_opaque(str(root / name), name))
    return frozenset(names)


def _held_from_tracked(parts, tracked, gate_files=None, catalog=()):
    held = set()
    prefixes = _prefix_docs(catalog)
    roots = _skill_roots(catalog)
    for fixed in _fixed_docs(catalog):
        if any(_covers(part, fixed) for part in parts):
            held.add(fixed)
    if tracked is not None:
        for part in parts:
            if _is_skill_file(part, roots):
                held.add(part)
        files = gate_files if gate_files is not None else tracked.files
        for path in files:
            if path.endswith("/"):
                continue
            covered = any(_covers(part, path) for part in parts)
            if not covered:
                continue
            if _is_skill_file(path, roots) or any(path.startswith(prefix) for prefix in prefixes):
                held.add(path)
        return tuple(sorted(held))
    for part in parts:
        if _is_skill_file(part, roots):
            held.add(part)
        for root in roots:
            stripped = root.rstrip("/")
            if part == "" or part == stripped or stripped.startswith(part + "/"):
                held.add(root)
            elif part.startswith(root) and "/" not in part[len(root):]:
                held.add(part + "/SKILL.md")
        for prefix in prefixes:
            stripped = prefix.rstrip("/")
            if part == "" or part == stripped or stripped.startswith(part + "/") or part.startswith(prefix):
                held.add(prefix)
    return tuple(sorted(held))


@dataclass(frozen=True)
class DocRefused:
    line: str


@dataclass(frozen=True)
class DocAllowed:
    _note: str | None

    def record(self, restaurant, dish_id):
        if self._note is None:
            return
        restaurant.log("doc", dish_id, "item", self._note)


@dataclass(frozen=True)
class PreparedDocFire:
    _held: tuple[str, ...]
    _minutes: int
    _timebox: int
    _ticket_count: int
    _doc_item: bool

    def decide(self):
        held = tuple(sorted(self._held))
        if not held:
            return DocAllowed("doc-item" if self._doc_item else None)
        if self._doc_item:
            return DocAllowed("doc-item: " + ", ".join(held))
        if not (self._timebox > self._minutes or self._ticket_count > 1):
            return DocAllowed(None)
        if len(held) == 1:
            docs = f"{held[0]} is a shared document"
        elif len(held) == 2:
            docs = f"{held[0]} and {held[1]} are shared documents"
        else:
            docs = f"{', '.join(held[:-1])}, and {held[-1]} are shared documents"
        reasons = []
        if self._timebox > self._minutes:
            reasons.append(f"--timebox is {self._timebox}, over {self._minutes} minutes")
        if self._ticket_count > 1:
            reasons.append(f"--tickets names {self._ticket_count} tickets")
        reason = ", and ".join(reasons)
        drop = "drop it" if len(held) == 1 else "drop them"
        return DocRefused(f"{docs} and {reason}; {drop} from --paths and give the sentences as follow-ups, or pass --doc-item")


def prepare_doc_fire(project_root, paths, timebox, ticket_count, doc_item, policy):
    parts = _lease_parts(paths)
    if not parts:
        return PreparedDocFire((), policy.minutes, timebox, ticket_count, doc_item)
    if not policy.docs:
        return PreparedDocFire((), policy.minutes, timebox, ticket_count, doc_item)
    if not (doc_item or timebox > policy.minutes or ticket_count > 1):
        return PreparedDocFire((), policy.minutes, timebox, ticket_count, doc_item)
    if not _can_cover_catalog(parts, policy.docs):
        return PreparedDocFire((), policy.minutes, timebox, ticket_count, doc_item)
    tracked = tracked_paths(project_root)
    gate_files = _gate_inventory(project_root) if tracked is not None else None
    return PreparedDocFire(_held_from_tracked(parts, tracked, gate_files, policy.docs), policy.minutes, timebox, ticket_count,
                           doc_item)
