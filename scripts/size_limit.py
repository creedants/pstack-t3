#!/usr/bin/env python3
"""Report worktree files over the file size limit.

This bridge loads the pure sizes module with spec_from_file_location, so it
needs no landing imports. check.py appends these findings when run without a tree argument. The
limit runs when the landing contract for the repo sets a size limit, or when the
committed baseline file explicitly opts in with enabled true. A baseline filename
alone never turns the limit on. A baseline that is not valid is one
finding.
"""

import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import stat
import subprocess
from pathlib import Path

ROOT_SIZES = Path(__file__).resolve().parents[1] / "t3/added/landing/scripts/landing_lib/sizes.py"


def load_sizes(path=None):
    """The sizes module loaded from its file, without a landing import."""
    file_path = Path(path) if path is not None else ROOT_SIZES
    spec = importlib.util.spec_from_file_location("pstack_sizes", file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_contract(repo):
    """Landing contract for this repo, or {}. Never raises."""
    try:
        proc = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                              cwd=repo, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return {}
        common = Path(proc.stdout.strip()).resolve()
        name = common.parent.name if common.name == ".git" else common.name.removesuffix(".git")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + "-" + hashlib.sha1(str(common).encode()).hexdigest()[:8]
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "pstack-t3" / "landing" / slug
        db_path = base / "land.db"
        if not db_path.is_file():
            return {}
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            rows = connection.execute("SELECT key, value FROM contract").fetchall()
        finally:
            connection.close()
        return {key: json.loads(value) for key, value in rows}
    except Exception:
        return {}


def _git(root, *args):
    try:
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    return proc


def _base_commit(root, contract):
    """Authoritative base commit for policy comparison, or None. Never raises."""
    candidates = []
    trunk = contract.get("trunk") if isinstance(contract, dict) else None
    if isinstance(trunk, str) and trunk:
        candidates.append(f"origin/{trunk}")
        candidates.append(trunk)
    candidates.extend(["origin/main", "main"])
    head = _git(root, "rev-parse", "--verify", "HEAD")
    if head is None or head.returncode != 0:
        return None
    for ref in candidates:
        tip = _git(root, "rev-parse", "--verify", ref)
        if tip is None or tip.returncode != 0:
            continue
        based = _git(root, "merge-base", "HEAD", ref)
        if based is not None and based.returncode == 0 and based.stdout.strip():
            return based.stdout.strip()
    return None


def _show_at(root, commit, rel):
    proc = _git(root, "show", f"{commit}:{rel}")
    if proc is None or proc.returncode != 0:
        return None, True
    try:
        return json.loads(proc.stdout), False
    except json.JSONDecodeError:
        return None, False


def _generated_list(contract):
    generated = contract.get("generated") or [] if isinstance(contract, dict) else []
    if not isinstance(generated, list) or any(not isinstance(entry, str) for entry in generated):
        return []
    return generated


def _walk_sizes(root):
    """Byte counts of regular files under this root, keyed by POSIX relative path."""
    sizes = {}
    for current, dirs, files in os.walk(root):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in files:
            full = Path(current) / name
            try:
                kind = full.stat()
            except OSError:
                continue
            if not stat.S_ISREG(kind.st_mode):
                continue
            sizes[full.relative_to(root).as_posix()] = kind.st_size
    return sizes


def findings(root):
    """Size findings for one repo worktree. Empty when the limit is off."""
    sizes = load_sizes()
    root = Path(root)
    contract = read_contract(root)
    baseline_rel = contract.get("sizeBaseline") or sizes.DEFAULT_BASELINE
    if not isinstance(baseline_rel, str) or not baseline_rel:
        baseline_rel = sizes.DEFAULT_BASELINE
    baseline_path = Path(baseline_rel) if Path(baseline_rel).is_absolute() else root / baseline_rel
    maximum = contract.get("sizeMaxTokens")
    contract_on = isinstance(maximum, int) and not isinstance(maximum, bool) and maximum >= 1
    if baseline_path.is_file():
        try:
            stored = sizes.load_baseline_file(str(baseline_path))
        except ValueError as error:
            return [f"{baseline_rel}: {error}"]
        try:
            work_raw = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            work_raw = None
        if not contract_on and not sizes.baseline_enabled(work_raw):
            return []
        base = _base_commit(root, contract)
        if base is not None:
            base_raw, missing = _show_at(root, base, baseline_rel)
            if not missing:
                problem = sizes.refused_policy_change(base_raw, work_raw)
                if problem:
                    return [f"size limit: {problem}"]
                try:
                    base_limit = sizes.parse_limit(base_raw) if isinstance(base_raw, dict) else stored
                except ValueError as error:
                    return [f"{baseline_rel}: {error}"]
                effective = maximum if contract_on else base_limit.max_tokens
                limit = sizes.Limit(effective, tuple(base_limit.skip), dict(base_limit.files))
                skip = tuple(sizes.DEFAULT_SKIP) + tuple(limit.skip) + tuple(_generated_list(contract)) + (baseline_rel,)
                limit = sizes.Limit(limit.max_tokens, skip, limit.files)
                try:
                    current = _walk_sizes(root)
                except OSError as error:
                    return [f"size limit: worktree cannot be read: {error}"]
                verdict = sizes.judge(current, limit)
                return [f"size limit: {sizes.format_over(item)}" for item in verdict.over]
        if not contract_on:
            maximum = stored.max_tokens
        limit = sizes.Limit(maximum, tuple(stored.skip), dict(stored.files))
    elif contract_on:
        limit = sizes.Limit(maximum, sizes.DEFAULT_SKIP, {})
    else:
        return []
    skip = tuple(sizes.DEFAULT_SKIP) + tuple(limit.skip) + tuple(_generated_list(contract)) + (baseline_rel,)
    limit = sizes.Limit(limit.max_tokens, skip, limit.files)
    try:
        current = _walk_sizes(root)
    except OSError as error:
        return [f"size limit: worktree cannot be read: {error}"]
    verdict = sizes.judge(current, limit)
    return [f"size limit: {sizes.format_over(item)}" for item in verdict.over]
