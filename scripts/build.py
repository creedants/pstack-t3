#!/usr/bin/env python3
"""Generate skills/ from vendor/pstack plus the T3 layer in t3/.

vendor/pstack      upstream pstack, byte-identical to upstream.json's commit
t3/overrides/      whole-file replacements, keyed by skill-relative path
t3/agents/         personas copied into skills/pstack-runtime/agents/, keyed agents/<name>.md
t3/added/          files with no upstream counterpart
t3/removed.txt     upstream paths that pstack-t3 does not ship
t3/overrides.lock.json  sha256 of vendored counterparts for skill overrides and personas

An override or a persona whose vendored file changed since it was written
fails the build until it is reviewed or re-ported and the lock refreshed
with --update-lock. Refreshing the lock omits personas with no vendored
counterpart. A stale persona pin reports drift until the lock is refreshed.
"""

import argparse
import hashlib
import json
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor/pstack"
T3 = ROOT / "t3"
LOCK = T3 / "overrides.lock.json"
DROP_KEYS = ("disable-model-invocation", "mode", "icon", "color", "reminder", "paths")
RUNTIME_POINTER = (
    "Read [the pstack-t3 runtime](../pstack-runtime/SKILL.md) before spawning workers, "
    "choosing models, scheduling, or isolating work. It maps those steps onto T3's orchestrator tools.\n\n"
)
NO_POINTER = re.compile(r"^(principle-|pstack-runtime$|bro$|ketchup$|unslop$|technical-writing$|typescript-best-practices$|tdd$)")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative_files(base):
    return sorted(p.relative_to(base) for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def persona_files():
    return sorted((T3 / "agents").glob("*.md"))


@dataclass(frozen=True)
class Replacement:
    """One file under t3 that may replace a vendored one, and every path that names it.

    lock_key is the key in t3/overrides.lock.json, a skill-relative path for an
    override and agents/<name>.md for a persona. problem_label is what the build
    names when the replacement drifts. conflict_rel is the path under the
    directory --conflicts writes to, so it equals lock_key. Refreshing the lock
    records the vendored digest for a persona with a counterpart and omits a
    persona with no counterpart. A stale persona pin still reports drift.
    """

    lock_key: str
    problem_label: str
    t3_path: str
    upstream_rel: str
    conflict_rel: str

    @property
    def pins(self):
        return (VENDOR / self.upstream_rel).is_file()

    @property
    def persona(self):
        return self.lock_key.startswith("agents/")


def replacements():
    """Every replacement the lock may carry, overrides first, then personas."""
    rows = []
    for rel in relative_files(T3 / "overrides"):
        if rel.parts[0] == "agents":
            raise SystemExit(
                f"t3/overrides/{rel.as_posix()}: a persona belongs in t3/agents; "
                "the lock reserves the agents/ prefix, so skill overrides under t3/overrides/agents are unsupported"
            )
        key = rel.as_posix()
        rows.append(Replacement(key, key, f"t3/overrides/{key}", f"skills/{key}", key))
    for persona in persona_files():
        key = f"agents/{persona.name}"
        rows.append(Replacement(key, f"t3/agents/{persona.name}", f"t3/agents/{persona.name}", key, key))
    return rows


def removed_paths():
    file = T3 / "removed.txt"
    if not file.exists():
        return set()
    return {Path(line.strip()) for line in file.read_text().splitlines() if line.strip() and not line.startswith("#")}


def normalize_skill(text, skill):
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not match:
        raise ValueError(f"{skill}/SKILL.md: missing frontmatter")
    lines = []
    for line in match.group(1).splitlines():
        key = line.split(":", 1)[0].strip()
        if key in DROP_KEYS:
            continue
        if key == "name":
            line = f"name: {skill}"
        lines.append(line)
    body = text[match.end():].lstrip("\n")
    if not NO_POINTER.match(skill) and RUNTIME_POINTER.strip() not in body:
        heading, newline, rest = body.partition("\n")
        body = heading + newline + "\n" + RUNTIME_POINTER + rest.lstrip("\n") if heading.startswith("# ") else RUNTIME_POINTER + body
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body


def entry_noun(lock_key):
    """The word a drift message uses for the entry a lock key names."""
    return "persona" if lock_key.startswith("agents/") else "override"


def check_lock(update):
    rows = replacements()
    by_key = {row.lock_key: row for row in rows}
    current = {}
    for row in rows:
        if row.pins:
            current[row.lock_key] = sha256(VENDOR / row.upstream_rel)
        elif not row.persona:
            current[row.lock_key] = None
    if update:
        LOCK.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        return []
    locked = json.loads(LOCK.read_text()) if LOCK.exists() else {}
    problems = []
    for key, digest in current.items():
        if key not in locked:
            problems.append(
                f"{by_key[key].problem_label}: {entry_noun(key)} not in overrides.lock.json "
                "(run build.py --update-lock after reviewing)"
            )
        elif locked[key] != digest:
            problems.append(
                f"{by_key[key].problem_label}: upstream changed since this {entry_noun(key)} was written; "
                "review or re-port it, then --update-lock"
            )
    for key in locked:
        if key in current:
            continue
        # A locked key missing from the current digests that still has its T3 file is a persona with no
        # vendored counterpart. Any other locked key names an entry with no T3 file.
        row = by_key.get(key)
        noun = entry_noun(key)
        detail = (
            f"upstream changed since this {noun} was written; review or re-port it, then --update-lock"
            if row
            else f"locked but no {noun} exists (run --update-lock)"
        )
        problems.append(f"{row.problem_label if row else key}: {detail}")
    return problems


def copy_file(source, target):
    """Copy source to target with source's mode plus owner write.

    A later layer and normalize_skill rewrite the target, which a mode copied from a read-only tree would refuse.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(stat.S_IMODE(source.stat().st_mode) | stat.S_IWUSR)


BRIGADE_LIB = ("__init__", "seam", "shared", "store", "reports", "tickets", "leases", "edits", "reviews", "items",
               "briefs", "wake", "reporting")


def check_brigade_lib(base):
    """The brigade_lib files the build requires. A missing one fails the build."""
    if not (base / "brigade" / "scripts" / "brigade.py").is_file():
        return
    missing = [f"brigade_lib/{name}.py" for name in BRIGADE_LIB
               if not (base / "brigade" / "scripts" / "brigade_lib" / f"{name}.py").is_file()]
    if missing:
        raise SystemExit("brigade build is missing " + ", ".join(missing))


def render(destination):
    removed = removed_paths()
    skills = VENDOR / "skills"
    for rel in relative_files(skills):
        if rel in removed or any(parent in removed for parent in rel.parents):
            continue
        copy_file(skills / rel, destination / rel)
    for layer in ("overrides", "added"):
        base = T3 / layer
        for rel in relative_files(base):
            copy_file(base / rel, destination / rel)
    check_brigade_lib(T3 / "added")
    check_brigade_lib(destination)
    runtime = destination / "pstack-runtime"
    copy_file(T3 / "runtime.md", runtime / "SKILL.md")
    copy_file(T3 / "scripts/roles.py", runtime / "scripts/roles.py")
    for module in sorted((T3 / "scripts/_roles").glob("*.py")):
        copy_file(module, runtime / "scripts/_roles" / module.name)
    for persona in sorted((T3 / "agents").glob("*.md")):
        copy_file(persona, runtime / "agents" / persona.name)
    copy_file(T3 / "setup.md", destination / "setup-pstack" / "SKILL.md")
    for skill_md in sorted(destination.glob("*/SKILL.md")):
        skill = skill_md.parent.name
        skill_md.write_text(normalize_skill(skill_md.read_text(), skill))


def build(destination, update_lock=False, skip_lock=False):
    problems = [] if skip_lock else check_lock(update_lock)
    if problems:
        raise SystemExit("override drift:\n  " + "\n  ".join(problems))
    with tempfile.TemporaryDirectory(prefix="pstack-t3-build-") as temporary:
        staged = Path(temporary) / "skills"
        render(staged)
        sys.path.insert(0, str(ROOT / "scripts"))
        from check import badge_findings, check_tree
        from catalog import write

        findings = check_tree(staged) + badge_findings(ROOT / "README.md", ROOT / "upstream.json")
        if findings:
            raise SystemExit("check failed:\n  " + "\n  ".join(findings))
        replace_tree(staged, destination)
    if destination == ROOT / "skills":
        import cli_reference

        write(destination)
        cli_reference.write()
    return destination


def replace_tree(source, destination):
    """Swap destination for a full copy of source, keeping the old tree until the copy exists."""
    incoming = destination.with_name(destination.name + ".incoming")
    outgoing = destination.with_name(destination.name + ".outgoing")
    if outgoing.exists() and not destination.exists():
        # A previous swap died between renames; its old tree is the only good copy.
        outgoing.rename(destination)
    for leftover in (incoming, outgoing):
        if leftover.exists():
            shutil.rmtree(leftover)
    shutil.copytree(source, incoming)
    if destination.exists():
        destination.rename(outgoing)
    incoming.rename(destination)
    if outgoing.exists():
        shutil.rmtree(outgoing)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update-lock", action="store_true", help="record digests for overrides and personas with a counterpart and null for overrides without one, omitting personas without one")
    parser.add_argument("--skip-lock", action="store_true", help="check a work-in-progress port without the drift lock")
    parser.add_argument("--out", type=Path, default=ROOT / "skills")
    args = parser.parse_args()
    print(f"built {build(args.out, args.update_lock, args.skip_lock)}")
