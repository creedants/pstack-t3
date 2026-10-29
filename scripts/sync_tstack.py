#!/usr/bin/env python3
"""Show what changed in the pinned tstack skills since the commit tstack.json pins, and bump the pin on request.

With no flag, resolve --ref (default main) to a commit, print a unified diff of each skill tstack.json lists
between the pinned commit and that commit, and write nothing. A skill with no change prints one
`<path>: unchanged` line.

With --bump, also write the new commit into tstack.json and rewrite every tstack link under t3/ and docs/ that
names the old commit. --bump refuses, and writes nothing, when a listed skill is missing at the new commit.
Run `python3 scripts/build.py` afterwards so skills/ carries the new links.

GitHub is read only through `gh api` GET requests.
"""

import argparse
import base64
import difflib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check import TSTACK_LINK  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("t3", "docs")


def gh_api(path):
    """The parsed JSON of one `gh api` GET request, or None when GitHub answers 404."""
    result = subprocess.run(["gh", "api", "--method", "GET", path], capture_output=True, text=True)
    if result.returncode:
        if "HTTP 404" in result.stderr:
            return None
        raise SystemExit(f"sync_tstack: gh api {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def slug(repository):
    """owner/name from the repository URL tstack.json records."""
    return repository.removesuffix(".git").rstrip("/").split("github.com/", 1)[1]


def skill_text(gh, name, path, commit):
    """The skill file's text at commit, or None when the path is missing there."""
    found = gh(f"repos/{name}/contents/{path}?ref={commit}")
    return None if found is None else base64.b64decode(found["content"]).decode("utf-8")


def rewrite_links(root, old, new):
    """Rewrite every tstack link under SOURCES that names old to name new. Returns the changed paths."""
    changed = []
    for top in SOURCES:
        for path in sorted((root / top).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            text = path.read_bytes().decode("utf-8", errors="replace")
            fixed = TSTACK_LINK.sub(lambda match: match.group(0).replace(f"/{old}/", f"/{new}/") if match.group(1) == old else match.group(0), text)
            if fixed != text:
                path.write_text(fixed, encoding="utf-8")
                changed.append(path.relative_to(root).as_posix())
    return changed


def main(argv=None, root=ROOT, gh=gh_api):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="main", help="tstack branch, tag, or commit to compare with (default main)")
    parser.add_argument("--bump", action="store_true", help="pin the --ref commit in tstack.json and rewrite every link that names the old one")
    args = parser.parse_args(argv)
    pin_path = Path(root) / "tstack.json"
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    name, old = slug(pin["repository"]), pin["commit"]
    commit = gh(f"repos/{name}/commits/{args.ref}")
    if commit is None:
        raise SystemExit(f"sync_tstack: {args.ref} is not a ref of {name}")
    new = commit["sha"]
    print(f"tstack {old} -> {new}")
    missing = []
    for path in pin["skills"]:
        before, after = skill_text(gh, name, path, old), skill_text(gh, name, path, new)
        if after is None:
            missing.append(path)
            print(f"{path}: missing at {new}")
        elif before == after:
            print(f"{path}: unchanged")
        else:
            sys.stdout.writelines(difflib.unified_diff((before or "").splitlines(True), after.splitlines(True),
                                                       f"{path}@{old}", f"{path}@{new}"))
    if not args.bump:
        return 0
    if missing:
        print(f"sync_tstack: not bumped; {', '.join(missing)} missing at {new}", file=sys.stderr)
        return 1
    if new == old:
        print("pin unchanged")
        return 0
    changed = rewrite_links(Path(root), old, new)
    pin["commit"] = new
    pin_path.write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")
    print(f"pinned {new} in tstack.json; rewrote {', '.join(changed) or 'no links'}; run python3 scripts/build.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
