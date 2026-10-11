#!/usr/bin/env python3
"""Copy generated pstack skills and activate an owned, reversible dispatcher.

No environment overrides are inferred. --home names the only writable root.
Transactions stop after a hard interruption and status names their recovery data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

HARNESSES = {"codex": (".codex", "AGENTS.md"), "claude": (".claude", "CLAUDE.md")}
BEGIN = "<!-- pstack-t3:activation:begin -->"
END = "<!-- pstack-t3:activation:end -->"
ESSENTIAL = {"poteto-mode", "project", "pstack-runtime"}
UNSPECIFIED = object()


class ActivationError(RuntimeError):
    pass


def exists(path):
    return os.path.lexists(path)


def linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def safe_path(path, root, *, leaf_link=False):
    path = Path(os.path.abspath(path))
    if path == root or root not in path.parents:
        raise ActivationError(f"Path is outside the named root or is the root itself: {path}")
    for part in (path, *path.parents):
        if linked(part) and not (part == path and leaf_link and part.is_symlink()):
            raise ActivationError(f"Linked path component refused: {part}")
    return path


def skill_name(name):
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name) or name in reserved:
        raise ActivationError(f"Unsafe skill name: {name!r}")
    return name


def content_hash(path, *, allow_links=False):
    """Hash entry type, relative names and bytes, without following links."""
    if not exists(path):
        return None
    digest = hashlib.sha256()

    def visit(entry, relative):
        if hasattr(entry, "is_junction") and entry.is_junction():
            raise ActivationError(f"Junction refused: {entry}")
        mode = entry.lstat().st_mode
        if stat.S_ISLNK(mode):
            if not allow_links:
                raise ActivationError(f"Symlink refused: {entry}")
            kind, data = b"L", os.fsencode(os.readlink(entry))
        elif stat.S_ISREG(mode):
            kind, data = b"F", entry.read_bytes()
        elif stat.S_ISDIR(mode):
            kind, data = b"D", b""
        else:
            raise ActivationError(f"Unsupported filesystem entry: {entry}")
        name = relative.encode("utf-8")
        digest.update(kind + len(name).to_bytes(8, "big") + name)
        digest.update(len(data).to_bytes(8, "big") + data)
        if kind == b"D":
            for child in sorted(entry.iterdir(), key=lambda item: item.name):
                if child.name == "__pycache__" or child.suffix in {".pyc", ".pyo"}:
                    continue
                visit(child, f"{relative}/{child.name}")

    visit(Path(path), "")
    return digest.hexdigest()


def copy_entry(source, target):
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    else:
        shutil.copy2(source, target)


def remove_entry(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif exists(path):
        shutil.rmtree(path)


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    if exists(temporary):
        raise ActivationError(f"Unfinished state write: {temporary}")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class Activation:
    def __init__(self, checkout: Path, home: Path, harnesses: tuple = ("codex",)):
        self.checkout = Path(os.path.abspath(checkout))
        self.home = Path(os.path.abspath(home))
        self.harnesses = tuple(dict.fromkeys(harnesses))
        if not self.harnesses or any(item not in HARNESSES for item in self.harnesses):
            raise ActivationError(f"Supported harnesses are {', '.join(HARNESSES)}")
        if self.home.parent == self.home:
            raise ActivationError("A filesystem root cannot be the activation home")
        if self.home == self.checkout or self.checkout in self.home.parents:
            raise ActivationError("Activation home must not overlap the read-only checkout")
        safe_path(self.home / ".pstack-t3", self.home)
        self.state = self.home / ".pstack-t3" / "activation"
        mutable = [self.state]
        for harness in self.harnesses:
            skills, instructions, _ = self._paths(harness)
            mutable.extend((skills, instructions))
        for path in mutable:
            if path == self.checkout or path in self.checkout.parents or self.checkout in path.parents:
                raise ActivationError(f"Activation path must not overlap the read-only checkout: {path}")

    def _paths(self, harness):
        directory, instructions = HARNESSES[harness]
        return self.home / directory / "skills", self.home / directory / instructions, self.state / harness

    def _check_paths(self, harness):
        skills, instructions, state = self._paths(harness)
        for path in (skills, instructions, state, state / "manifest.json", state / "backups", state / "lock", state / "pending"):
            safe_path(path, self.home)
        for directory in (self.home, skills.parent, skills, self.state, state, state / "backups"):
            if exists(directory) and not directory.is_dir():
                raise ActivationError(f"Expected a directory: {directory}")
        for path in (state / "manifest.json", state / "lock", state / "manifest.json.tmp"):
            if exists(path) and not path.is_file():
                raise ActivationError(f"Expected a state file: {path}")
        if exists(instructions) and not instructions.is_file():
            raise ActivationError(f"Expected an instruction file: {instructions}")
        return skills, instructions, state

    def _load(self, harness):
        _, _, state = self._check_paths(harness)
        path = state / "manifest.json"
        if not exists(path):
            return None
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest["version"] != 1 or manifest["home"] != str(self.home) or manifest["harness"] != harness:
                raise ValueError("manifest identity mismatch")
            if manifest["checkout"] != str(self.checkout):
                raise ValueError("activation belongs to another checkout")
            # Earlier selected installs omitted project; retain their restoration authority.
            if not isinstance(manifest["skills"], dict) or not {"poteto-mode", "pstack-runtime"} <= manifest["skills"].keys():
                raise ValueError("invalid skill records")
            for name, record in manifest["skills"].items():
                skill_name(name)
                for key in ("hash", "backup_hash"):
                    value = record[key]
                    if not (value is None and key == "backup_hash") and not (isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)):
                        raise ValueError("invalid content hash")
            block = manifest["block"]
            skills, _, _ = self._paths(harness)
            if block not in (self._block(skills), "\n" + self._block(skills)):
                raise ValueError("invalid managed block")
            if type(manifest["instruction_existed"]) is not bool:
                raise ValueError("invalid instruction ownership")
            return manifest
        except (KeyError, TypeError, ValueError) as error:
            raise ActivationError(f"Invalid manifest {path}: {error}") from error

    def _gaps(self, harness, manifest, *, locked=False):
        skills, instructions, state = self._check_paths(harness)
        gaps = []
        pending_paths = (state / "pending", state / "manifest.json.tmp") + (() if locked else (state / "lock",))
        for pending in pending_paths:
            if exists(pending):
                gaps.append(f"Interrupted or active operation at {pending}. Retain its journal, stages and saved entries; inspect before recovery.")
        if manifest is None:
            if exists(state / "backups") and any((state / "backups").iterdir()):
                gaps.append(f"Backups without an ownership manifest at {state / 'backups'}")
            data = instructions.read_bytes() if exists(instructions) else b""
            if BEGIN.encode() in data or END.encode() in data:
                gaps.append(f"Unowned activation markers in {instructions}")
            return gaps
        for name, record in manifest["skills"].items():
            target = safe_path(skills / name, self.home, leaf_link=True)
            try:
                actual = content_hash(target)
            except ActivationError as error:
                gaps.append(str(error))
                actual = None
            if actual != record["hash"]:
                gaps.append(f"Managed skill missing or edited: {target}")
            backup = safe_path(state / "backups" / name, self.home, leaf_link=True)
            if content_hash(backup, allow_links=True) != record["backup_hash"]:
                gaps.append(f"Backup missing or edited: {backup}")
        data = instructions.read_bytes() if exists(instructions) else b""
        block = manifest["block"].encode("utf-8")
        if data.count(block) != 1 or data.count(BEGIN.encode()) != 1 or data.count(END.encode()) != 1:
            gaps.append(f"Managed instruction block missing or edited: {instructions}")
        return gaps

    def status(self):
        result = {}
        for harness in self.harnesses:
            try:
                manifest = self._load(harness)
                gaps = self._gaps(harness, manifest)
                skills, instructions, state = self._paths(harness)
                result[harness] = {
                    "state": "gap" if gaps else "installed" if manifest else "not-installed",
                    "skills": sorted(manifest["skills"]) if manifest else [],
                    "skill_directory": str(skills), "instructions": str(instructions),
                    "manifest": str(state / "manifest.json"), "gaps": gaps,
                }
            except (ActivationError, OSError) as error:
                result[harness] = {"state": "gap", "gaps": [str(error)]}
        return result

    def _block(self, skills):
        link = (skills / "poteto-mode" / "SKILL.md").as_posix().replace("<", "%3C").replace(">", "%3E")
        return (
            f"{BEGIN}\n"
            f"For substantive T3/Codex tasks, use the installed [Poteto SKILL](<{link}>) by default.\n"
            "When a project exists, restore its persistent project context first through the installed project workflow.\n"
            "Choose the smallest effective strategy for the task: direct, delegate, swarm, mesh, or arena.\n"
            "Honor explicit modes and opt-out. Handle trivial or casual questions directly without new agents.\n"
            "Use the live model catalog when choosing models. This default grants no additional permissions.\n"
            f"{END}\n"
        )

    def _sources(self, selected):
        root = self.checkout / "skills"
        safe_path(root, self.checkout)
        if not root.is_dir():
            raise ActivationError(f"Build generated skills first: {root}")
        names = {skill_name(name) for name in selected} | ESSENTIAL if selected is not None else {skill_name(path.name) for path in root.iterdir() if path.name != "__pycache__"}
        if not ESSENTIAL <= names:
            raise ActivationError("Generated poteto-mode, project and pstack-runtime skills are required")
        sources = {}
        for name in sorted(names):
            source = safe_path(root / name, self.checkout)
            if not source.is_dir() or not (source / "SKILL.md").is_file():
                raise ActivationError(f"Missing generated skill: {source}")
            sources[name] = content_hash(source)
        return sources

    def _preflight(self, harness):
        manifest = self._load(harness)
        gaps = self._gaps(harness, manifest)
        if gaps:
            raise ActivationError("\n".join(gaps))
        return manifest

    @contextmanager
    def _lock(self, harness):
        _, _, state = self._check_paths(harness)
        state.mkdir(parents=True, exist_ok=True)
        lock = state / "lock"
        try:
            with lock.open("x", encoding="utf-8") as stream:
                stream.write(f"pid={os.getpid()}\n")
        except FileExistsError as error:
            raise ActivationError(f"Active or interrupted activation lock: {lock}") from error
        try:
            yield
        finally:
            lock.unlink()

    def _apply_change(self, pending, changes, target, source=None, data=None, *, move=False, expected_before=UNSPECIFIED):
        target = safe_path(target, self.home, leaf_link=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        index = len(changes)
        saved = pending / f"saved-{index}"
        stage = target.with_name(f".pstack-t3-{uuid.uuid4().hex}")
        before = content_hash(target, allow_links=True)
        if expected_before is not UNSPECIFIED and before != expected_before:
            raise ActivationError(f"Target changed before replacement: {target}")
        after = None
        if move:
            safe_path(source, self.home, leaf_link=True)
            after = content_hash(source, allow_links=True)
            if after is None:
                raise ActivationError(f"Move source missing: {source}")
        change = {"target": str(target), "saved": str(saved), "stage": str(stage), "before": before, "after": after, "move_source": str(source) if move else None}
        changes.append(change)
        write_json(pending / "journal.json", changes)
        if not move and source is not None:
            copy_entry(source, stage)
        elif data is not None:
            with stage.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if target.is_file() and not target.is_symlink():
                stage.chmod(stat.S_IMODE(target.stat().st_mode))
        if not move:
            change["after"] = content_hash(stage, allow_links=True)
        write_json(pending / "journal.json", changes)
        if content_hash(target, allow_links=True) != before:
            raise ActivationError(f"Target changed during staging: {target}")
        if move and content_hash(source, allow_links=True) != change["after"]:
            raise ActivationError(f"Move source changed: {source}")
        if exists(target):
            os.replace(target, saved)
        if move:
            os.replace(source, target)
        elif exists(stage):
            os.replace(stage, target)
        if content_hash(target, allow_links=True) != change["after"]:
            raise ActivationError(f"Replacement verification failed: {target}")

    def _rollback(self, pending, changes):
        for change in reversed(changes):
            target = safe_path(Path(change["target"]), self.home, leaf_link=True)
            saved = safe_path(Path(change["saved"]), self.home, leaf_link=True)
            stage = safe_path(Path(change["stage"]), self.home, leaf_link=True)
            current = content_hash(target, allow_links=True)
            if change["move_source"]:
                source = safe_path(Path(change["move_source"]), self.home, leaf_link=True)
                if not exists(source):
                    if current != change["after"]:
                        raise ActivationError(f"Rollback move ownership gap: {target}. Recovery data: {pending}")
                    os.replace(target, source)
                    current = None
                elif content_hash(source, allow_links=True) != change["after"]:
                    raise ActivationError(f"Rollback move source changed: {source}. Recovery data: {pending}")
            if exists(saved):
                if current not in (None, change["after"]):
                    raise ActivationError(f"Rollback refuses changed target: {target}. Recovery data: {pending}")
                remove_entry(target)
                os.replace(saved, target)
            elif current != change["before"]:
                if change["before"] is not None or current != change["after"]:
                    raise ActivationError(f"Rollback ownership gap: {target}. Recovery data: {pending}")
                remove_entry(target)
            if content_hash(target, allow_links=True) != change["before"]:
                raise ActivationError(f"Rollback verification failed: {target}. Recovery data: {pending}")
            remove_entry(stage)
        shutil.rmtree(pending)

    def _transaction(self, harness, operation, old, sources=None):
        skills, instructions, state = self._check_paths(harness)
        manifest_hash = content_hash(state / "manifest.json")
        pending = state / "pending"
        pending.mkdir()
        changes = []
        write_json(pending / "journal.json", changes)
        try:
            if operation == "install":
                records = dict(old["skills"]) if old else {}
                for name, expected in sources.items():
                    target = safe_path(skills / name, self.home, leaf_link=True)
                    backup = safe_path(state / "backups" / name, self.home, leaf_link=True)
                    previous = records.get(name)
                    if previous and previous["hash"] == expected:
                        continue
                    backup_hash = previous["backup_hash"] if previous else content_hash(target, allow_links=True)
                    if previous is None and backup_hash is not None:
                        if exists(backup):
                            raise ActivationError(f"Unowned backup already exists: {backup}")
                        self._apply_change(pending, changes, backup, source=target, move=True, expected_before=None)
                        if content_hash(backup, allow_links=True) != backup_hash:
                            raise ActivationError(f"Backup verification failed: {backup}")
                    self._apply_change(pending, changes, target, source=self.checkout / "skills" / name, expected_before=previous["hash"] if previous else None)
                    if content_hash(target) != expected:
                        raise ActivationError(f"Generated source changed during install: {name}")
                    records[name] = {"hash": expected, "backup_hash": backup_hash}
                instruction_hash = content_hash(instructions)
                instruction_existed = instruction_hash is not None
                data = instructions.read_bytes() if instruction_existed else b""
                block = old["block"] if old else ("\n" if data and not data.endswith(b"\n") else "") + self._block(skills)
                if old is None:
                    if BEGIN.encode() in data or END.encode() in data:
                        raise ActivationError(f"Unowned activation markers in {instructions}")
                    self._apply_change(pending, changes, instructions, data=data + block.encode("utf-8"), expected_before=instruction_hash)
                manifest = {
                    "version": 1, "checkout": str(self.checkout), "home": str(self.home), "harness": harness,
                    "skills": records, "block": block,
                    "instruction_existed": old["instruction_existed"] if old else instruction_existed,
                }
                encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
                if not old or manifest != old:
                    self._apply_change(pending, changes, state / "manifest.json", data=encoded, expected_before=manifest_hash)
                for name, record in records.items():
                    if content_hash(skills / name) != record["hash"] or content_hash(state / "backups" / name, allow_links=True) != record["backup_hash"]:
                        raise ActivationError(f"Final ownership verification failed: {name}")
                data = instructions.read_bytes()
                if data.count(block.encode("utf-8")) != 1 or data.count(BEGIN.encode()) != 1 or data.count(END.encode()) != 1 or self._load(harness) != manifest:
                    raise ActivationError("Final instruction or manifest verification failed")
            else:
                restored = {}
                for name, record in old["skills"].items():
                    backup = state / "backups" / name
                    self._apply_change(pending, changes, skills / name, source=backup if record["backup_hash"] else None, move=record["backup_hash"] is not None, expected_before=record["hash"])
                    restored[name] = record["backup_hash"]
                instruction_hash = content_hash(instructions)
                data = instructions.read_bytes()
                block = old["block"].encode("utf-8")
                if data.count(block) != 1 or data.count(BEGIN.encode()) != 1 or data.count(END.encode()) != 1:
                    raise ActivationError(f"Managed instruction block missing or edited: {instructions}")
                remaining = data.replace(block, b"", 1)
                self._apply_change(pending, changes, instructions, data=remaining if remaining or old["instruction_existed"] else None, expected_before=instruction_hash)
                for name, expected in restored.items():
                    if content_hash(skills / name, allow_links=True) != expected:
                        raise ActivationError(f"Restore verification failed: {skills / name}")
                self._apply_change(pending, changes, state / "manifest.json", expected_before=manifest_hash)
                if exists(state / "manifest.json") or (instructions.read_bytes() if exists(instructions) else b"") != remaining:
                    raise ActivationError("Uninstall verification failed")
        except Exception:
            self._rollback(pending, changes)
            raise
        # Saved entries remain available through verification of the whole operation.
        shutil.rmtree(pending)

    def install(self, skills=None, *, dry_run=False):
        sources = self._sources(skills)
        for harness in self.harnesses:
            old = self._preflight(harness)
            targets, _, state = self._paths(harness)
            for name in sources:
                content_hash(safe_path(targets / name, self.home, leaf_link=True), allow_links=True)
                backup = safe_path(state / "backups" / name, self.home, leaf_link=True)
                if (old is None or name not in old["skills"]) and exists(backup):
                    raise ActivationError(f"Unowned backup already exists: {backup}")
        if dry_run:
            return {h: {"state": "dry-run", "operation": "install", "skills": sorted(sources)} for h in self.harnesses}
        for harness in self.harnesses:
            with self._lock(harness):
                # The lock is ours. All other gap checks still run under it.
                old = self._load(harness)
                gaps = self._gaps(harness, old, locked=True)
                if gaps:
                    raise ActivationError("\n".join(gaps))
                self._transaction(harness, "install", old, sources)
        return self.status()

    def uninstall(self, *, dry_run=False):
        manifests = {h: self._preflight(h) for h in self.harnesses}
        if dry_run:
            return {h: {"state": "dry-run", "operation": "uninstall", "skills": sorted(m["skills"]) if m else []} for h, m in manifests.items()}
        for harness, old in manifests.items():
            if old is None:
                continue
            with self._lock(harness):
                current = self._load(harness)
                gaps = self._gaps(harness, current, locked=True)
                if gaps or current != old:
                    raise ActivationError("\n".join(gaps) or "Ownership changed before uninstall")
                self._transaction(harness, "uninstall", current)
        return self.status()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "status", "uninstall"))
    parser.add_argument("--home", type=Path, required=True, help="Explicit writable user home. Environment provider overrides are ignored.")
    parser.add_argument("--harness", action="append", choices=tuple(HARNESSES))
    parser.add_argument("--skills", nargs="+", help="Generated skill names. poteto-mode, project and pstack-runtime are always included.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[1], help="Checkout containing generated skills. Defaults to this script's parent checkout.")
    args = parser.parse_args(argv)
    if args.command != "install" and args.skills:
        parser.error("--skills applies only to install")
    try:
        activation = Activation(args.checkout, args.home, tuple(args.harness or ["codex"]))
        if args.command == "install":
            result = activation.install(args.skills, dry_run=args.dry_run)
        elif args.command == "uninstall":
            result = activation.uninstall(dry_run=args.dry_run)
        else:
            result = activation.status()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if any(value["state"] == "gap" for value in result.values()) else 0
    except (ActivationError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
