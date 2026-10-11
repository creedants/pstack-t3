#!/usr/bin/env python3
"""Replace vendor/pstack with a newer upstream revision and report override drift.

After syncing, `python3 scripts/build.py` lists every override whose upstream
file changed. Re-port each one, then run `build.py --update-lock`.

With --check, sync nothing. Print each path upstream changed since the pinned
commit and what pstack-t3 does with that path, and write nothing in the checkout.

With --merge, sync and, for each file in t3/overrides whose upstream file
changed, either write a clean three-way merge of it or leave the override as it
is and name it. The merge base is the file in vendor/pstack before the sync,
used only when its sha256 is the one in t3/overrides.lock.json. The lock is
never refreshed. With --merge --dry-run, print a row for each of those files
and write nothing in the checkout.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Mapping

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "upstream.json"

Change = Literal["changed", "added", "removed"]
Handling = Literal["overridden", "dropped", "unchanged", "not-shipped"]
Kind = Literal["clean", "same", "conflict", "markers", "deleted", "binary", "no-base"]

STATUS = {"M": "changed", "T": "changed", "A": "added", "D": "removed"}
HANDLING_TEXT = {
    "overridden": "overridden by {t3_path}",
    "dropped": "dropped by t3/removed.txt",
    "unchanged": "ships unchanged",
    "not-shipped": "not shipped",
}
MERGE_DETAIL = {
    "clean": "merged",
    "same": "the merge changes nothing, left as is",
    "conflict": "left as is, re-port by hand",
    "markers": "the merge result holds a line starting with <<<<<<<, >>>>>>>, or |||||||, left as is, re-port by hand",
    "deleted": "upstream deleted its file, left as is",
    "binary": "a NUL byte in one of the three texts, left as is, re-port by hand",
    "no-base": "no old upstream text that matches the lock, left as is, re-port by hand",
}
DRY_RUN_DETAIL = {**MERGE_DETAIL, "clean": "would merge"}
MERGE_FOOTER = """\
t3/overrides.lock.json is not refreshed.
A clean merge is not a reviewed port, because upstream's new lines can hold a Cursor mechanism.
Once the merges are written, review each merged override and re-port each override whose row says so. Then run python3 scripts/build.py --update-lock, which runs the build's Cursor-leftover check.
"""
# No "=======" here. That line also underlines a Markdown heading, and git writes it only between a "<<<<<<<" line and a ">>>>>>>" line.
MARKER_LINE = re.compile(rb"^(?:<{7}|>{7}|\|{7})", re.MULTILINE)
FIXED_COPIES = {
    "pstack-runtime/SKILL.md": "runtime.md",
    "pstack-runtime/scripts/roles.py": "scripts/roles.py",
    "setup-pstack/SKILL.md": "setup.md",
}


@dataclass(frozen=True)
class Layers:
    hand_ported: Mapping[str, str]
    added: frozenset[str]
    overrides: frozenset[str]
    removed: frozenset[str]


@dataclass(frozen=True)
class Entry:
    path: str
    change: Change
    handling: Handling
    t3_path: str | None


@dataclass(frozen=True)
class Report:
    repository: str
    ref: str
    pinned: str
    commit: str
    version: str | None
    changes: tuple[Entry, ...]

    @property
    def up_to_date(self):
        return self.commit == self.pinned


@dataclass(frozen=True)
class Outcome:
    rel: str
    kind: Kind
    merged: bytes | None = None


def run(*command, cwd=None):
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def load_layers():
    # Importing build would otherwise leave scripts/__pycache__ in the checkout.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "scripts"))
    import build

    t3 = build.T3
    hand_ported = {f"skills/{key}": f"t3/{file}" for key, file in FIXED_COPIES.items() if (t3 / file).is_file()}
    for persona in sorted((t3 / "agents").glob("*.md")):
        hand_ported[f"agents/{persona.name}"] = f"t3/agents/{persona.name}"
        hand_ported[f"skills/pstack-runtime/agents/{persona.name}"] = f"t3/agents/{persona.name}"
    return Layers(
        hand_ported=hand_ported,
        added=frozenset(rel.as_posix() for rel in build.relative_files(t3 / "added")),
        overrides=frozenset(rel.as_posix() for rel in build.relative_files(t3 / "overrides")),
        removed=frozenset(rel.as_posix() for rel in build.removed_paths()),
    )


def classify(path, prefix, layers):
    rest = PurePosixPath(path).relative_to(prefix)
    if "__pycache__" in rest.parts:
        return "not-shipped", None
    if str(rest) in layers.hand_ported:
        return "overridden", layers.hand_ported[str(rest)]
    if len(rest.parts) < 2 or rest.parts[0] != "skills":
        return "not-shipped", None
    key = PurePosixPath(*rest.parts[1:])
    if str(key) in layers.added:
        return "overridden", f"t3/added/{key}"
    if str(key) in layers.overrides:
        return "overridden", f"t3/overrides/{key}"
    if str(key) in layers.removed or any(str(parent) in layers.removed for parent in key.parents):
        return "dropped", None
    return "unchanged", None


def upstream_changes(repository, ref, pinned, prefix):
    with tempfile.TemporaryDirectory(prefix="pstack-upstream-") as temporary:
        clone = Path(temporary) / "upstream.git"

        def git(*arguments, cwd=clone):
            return subprocess.run(
                ("git", *arguments),
                cwd=cwd,
                check=True,
                capture_output=True,
                encoding="utf-8",
                errors="surrogateescape",
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            ).stdout

        git("clone", "--bare", "--filter=blob:none", repository, str(clone), cwd=temporary)
        commit = git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").strip()
        changes = []
        if commit != pinned:
            listing = git(
                "diff", "--name-status", "--no-renames", "--no-ext-diff", "-z", pinned, commit, "--", f":(literal){prefix}"
            )
            fields = listing.split("\0")[:-1]
            changes = [(STATUS[status], path) for status, path in zip(fields[::2], fields[1::2])]
        try:
            version = json.loads(git("show", f"{commit}:{prefix}/.cursor-plugin/plugin.json")).get("version")
        except (subprocess.CalledProcessError, ValueError, AttributeError):
            version = None
    return commit, version if isinstance(version, str) else None, changes


def quoted(text):
    return text if text.isprintable() and " " not in text else json.dumps(text)


def render_text(report, prefix):
    if report.up_to_date:
        return f"up to date ({report.pinned[:12]})\n"
    version = f" ({quoted(report.version)})" if report.version else ""
    lines = [f"upstream {report.ref}: {report.pinned[:12]} -> {report.commit[:12]}{version}"]
    for entry in report.changes:
        handling = HANDLING_TEXT[entry.handling].format(t3_path=quoted(entry.t3_path or ""))
        lines.append(f"{entry.change:<7}  {quoted(entry.path)}  {handling}")
    if not report.changes:
        lines.append(f"no changes under {prefix}/")
    return "\n".join(lines) + "\n"


def render_json(report):
    fields = asdict(report)
    changes = fields.pop("changes")
    return json.dumps({**fields, "up_to_date": report.up_to_date, "changes": changes}, indent=2) + "\n"


def check(args, meta):
    repository = args.repository or meta["repository"]
    try:
        commit, version, changes = upstream_changes(repository, args.ref, meta["commit"], meta["path"])
    except subprocess.CalledProcessError as error:
        print(error.stderr.strip() or error, file=sys.stderr)
        return 1
    except OSError as error:
        print(error, file=sys.stderr)
        return 1
    layers = load_layers()
    entries = (Entry(path, change, *classify(path, meta["path"], layers)) for change, path in changes)
    report = Report(
        repository, args.ref, meta["commit"], commit, version, tuple(sorted(entries, key=lambda entry: entry.path))
    )
    sys.stdout.write(render_json(report) if args.json else render_text(report, meta["path"]))
    return 0


def merge_file(current, base, other):
    result = subprocess.run(("git", "merge-file", "-p", str(current), str(base), str(other)), capture_output=True)
    if 1 <= result.returncode <= 127:
        return None
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, result.args, stderr=os.fsdecode(result.stderr))
    return result.stdout


def merge_outcome(rel, current, base, other, digest):
    old = base.read_bytes() if base.is_file() else None
    new = other.read_bytes() if other.is_file() else None
    if old == new:
        return None
    if new is None:
        return Outcome(rel, "deleted")
    if old is None or hashlib.sha256(old).hexdigest() != digest:
        return Outcome(rel, "no-base")
    mine = current.read_bytes()
    if any(b"\0" in text for text in (old, mine, new)):
        return Outcome(rel, "binary")
    merged = merge_file(current, base, other)
    if merged is None:
        return Outcome(rel, "conflict")
    if merged == mine:
        return Outcome(rel, "same")
    return Outcome(rel, "markers") if MARKER_LINE.search(merged) else Outcome(rel, "clean", merged)


def plan_merges(overrides, old_skills, new_skills, lock):
    import build

    outcomes = (
        merge_outcome(rel.as_posix(), overrides / rel, old_skills / rel, new_skills / rel, lock.get(rel.as_posix()))
        for rel in build.relative_files(overrides)
    )
    return tuple(outcome for outcome in outcomes if outcome)


def apply(outcomes, t3):
    # Staged beside the lock, so a crash leaves each override whole and no extra file under t3/overrides.
    staging = t3 / "overrides.merge-incoming"
    for outcome in outcomes:
        if outcome.kind == "clean":
            target = t3 / "overrides" / outcome.rel
            staging.write_bytes(outcome.merged)
            shutil.copymode(target, staging)
            os.replace(staging, target)


def render_merges(outcomes, dry_run):
    if not outcomes:
        return "no override's upstream file changed\n"
    detail = DRY_RUN_DETAIL if dry_run else MERGE_DETAIL
    return "".join(f"{outcome.kind:<8}  {quoted('t3/overrides/' + outcome.rel)}  {detail[outcome.kind]}\n" for outcome in outcomes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="main", help="upstream branch, tag, or commit")
    parser.add_argument("--check", action="store_true", help="report what upstream changed since the pinned commit and sync nothing")
    parser.add_argument("--json", action="store_true", help="with --check, print the report as one JSON object")
    parser.add_argument("--repository", help="with --check, the upstream to read (default: the repository in upstream.json)")
    parser.add_argument("--merge", action="store_true", help="sync and apply the three-way merge described above")
    parser.add_argument("--dry-run", action="store_true", help="with --merge, write nothing in the checkout")
    args = parser.parse_args()
    if not args.check and (args.json or args.repository):
        parser.error("--json and --repository need --check")
    if args.merge and args.check:
        parser.error("--merge does not combine with --check")
    if args.dry_run and not args.merge:
        parser.error("--dry-run needs --merge")
    meta = json.loads(UPSTREAM.read_text())
    if args.check:
        return check(args, meta)
    if args.merge:
        sys.dont_write_bytecode = True
    with tempfile.TemporaryDirectory(prefix="pstack-upstream-") as temporary:
        try:
            checkout = Path(temporary) / "plugins"
            run("git", "clone", "--filter=blob:none", "--no-checkout", meta["repository"], str(checkout))
            run("git", "sparse-checkout", "set", meta["path"], cwd=checkout)
            run("git", "checkout", args.ref, cwd=checkout)
            commit = run("git", "rev-parse", "HEAD", cwd=checkout)
            source = checkout / meta["path"]
            plugin = json.loads((source / ".cursor-plugin/plugin.json").read_text())
            sys.path.insert(0, str(ROOT / "scripts"))
            import build

            outcomes = ()
            if args.merge:
                lock = json.loads(build.LOCK.read_text()) if build.LOCK.exists() else {}
                outcomes = plan_merges(build.T3 / "overrides", build.VENDOR / "skills", source / "skills", lock)
        except (subprocess.CalledProcessError, OSError) as error:
            if not args.merge:
                raise
            stderr = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else ""
            print(stderr or error, file=sys.stderr)
            return 1
        if args.dry_run:
            version = plugin.get("version", meta.get("version"))
            print(f"vendor/pstack: {meta['commit'][:12]} -> {commit[:12]} ({version}), dry run, nothing written in the checkout")
            sys.stdout.write(render_merges(outcomes, dry_run=True) + (MERGE_FOOTER if outcomes else ""))
            return 0
        shutil.rmtree(source / ".git", ignore_errors=True)
        build.replace_tree(source, ROOT / "vendor/pstack")
    previous = meta["commit"]
    meta.update(commit=commit, version=plugin.get("version", meta.get("version")))
    UPSTREAM.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"vendor/pstack: {previous[:12]} -> {commit[:12]} ({meta['version']})")
    if args.merge:
        apply(outcomes, build.T3)
        sys.stdout.write(render_merges(outcomes, dry_run=False))
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build.py")], capture_output=True, text=True)
    print(result.stdout + result.stderr)
    sys.stdout.write(MERGE_FOOTER if outcomes else "")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
