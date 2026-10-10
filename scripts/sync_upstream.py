#!/usr/bin/env python3
"""Replace vendor/pstack with a newer upstream revision and report override drift.

After syncing, `python3 scripts/build.py` lists every override whose upstream
file changed. Re-port each one, then run `build.py --update-lock`.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "upstream.json"

STATUS = {"M": "changed", "T": "changed", "A": "added", "D": "removed"}
# The copies that end build.render(), so they win over every other layer.
# Skill-relative key -> file under t3/. render() also copies t3/agents/*.md.
FIXED_COPIES = {
    "pstack-runtime/SKILL.md": "runtime.md",
    "pstack-runtime/scripts/roles.py": "scripts/roles.py",
    "setup-pstack/SKILL.md": "setup.md",
}


@dataclass(frozen=True)
class Layers:
    """The files under t3/ that decide what ships, read once and never written."""

    hand_ported: Mapping[str, str]  # path under upstream's prefix -> the t3 file shipped in its place
    added: frozenset[str]  # skill-relative keys under t3/added
    overrides: frozenset[str]  # skill-relative keys under t3/overrides
    removed: frozenset[str]  # t3/removed.txt entries, each a file or a directory


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
    """What pstack-t3 does with an upstream path today, as (handling, t3 file or None).

    Reads build.render() backwards, because its last write wins.
    """
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
    """Return (commit at ref, its plugin version or None, [(change, path)] since pinned).

    Every git call and the temporary clone live here. Nothing is checked out.
    """
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="main", help="upstream branch, tag, or commit")
    args = parser.parse_args()
    meta = json.loads(UPSTREAM.read_text())
    with tempfile.TemporaryDirectory(prefix="pstack-upstream-") as temporary:
        checkout = Path(temporary) / "plugins"
        run("git", "clone", "--filter=blob:none", "--no-checkout", meta["repository"], str(checkout))
        run("git", "sparse-checkout", "set", meta["path"], cwd=checkout)
        run("git", "checkout", args.ref, cwd=checkout)
        commit = run("git", "rev-parse", "HEAD", cwd=checkout)
        source = checkout / meta["path"]
        plugin = json.loads((source / ".cursor-plugin/plugin.json").read_text())
        sys.path.insert(0, str(ROOT / "scripts"))
        from build import replace_tree

        shutil.rmtree(source / ".git", ignore_errors=True)
        replace_tree(source, ROOT / "vendor/pstack")
    previous = meta["commit"]
    meta.update(commit=commit, version=plugin.get("version", meta.get("version")))
    UPSTREAM.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"vendor/pstack: {previous[:12]} -> {commit[:12]} ({meta['version']})")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build.py")], capture_output=True, text=True)
    print(result.stdout + result.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
