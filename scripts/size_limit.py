#!/usr/bin/env python3
"""Report worktree files over the file size limit.

This bridge loads the pure sizes module with spec_from_file_location, so it
needs no landing imports. check.py appends these findings when run without a tree argument. The
limit runs when the landing contract for the repo sets a size limit, or when the
baseline file committed at the trunk base explicitly opts in with enabled true. A baseline filename
alone never turns the limit on. When the trunk base is readable, a baseline that does not parse or has an unreadable maximum or files map is one
finding while the limit is on. When the trunk base cannot be read, the bridge reads HEAD and reports size baseline at trunk tip cannot be read
while the limit is on, else stays off, without reading the candidate.
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
    """Byte counts of regular files, following symlinks, under this root, keyed by POSIX relative path."""
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
    contract_max_raw = contract.get("sizeMaxTokens")
    contract_on = isinstance(contract_max_raw, int) and not isinstance(contract_max_raw, bool) and contract_max_raw >= 1
    base = _base_commit(root, contract)
    if base is None:
        head = _git(root, "rev-parse", "--verify", "HEAD")
        head_exists = head is not None and head.returncode == 0
        if not head_exists:
            if contract_on:
                return [f"{baseline_rel}: size baseline at trunk tip cannot be read"]
            return []
        head_raw, head_missing = _show_at(root, "HEAD", baseline_rel)
        trusted_head = sizes.resolve_trusted(contract_max_raw, head_raw, head_missing, None)
        if trusted_head.enabled:
            return [f"{baseline_rel}: size baseline at trunk tip cannot be read"]
        return []
    else:
        base_raw, missing = _show_at(root, base, baseline_rel)
        trusted = sizes.resolve_trusted(contract_max_raw, base_raw, missing, None)
        if trusted.error:
            return [f"{baseline_rel}: {trusted.error}"]
        if not trusted.enabled:
            return []
        if not baseline_path.is_file():
            if not missing:
                return ["size limit: size baseline removes enabled policy; a person merges that raise outside the queue"]
        else:
            try:
                work_raw = json.loads(baseline_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return [f"{baseline_rel}: size baseline cannot be read"]
            if contract_on:
                if not missing and isinstance(base_raw, dict):
                    raw_max = base_raw.get("maxFileTokens", sizes.DEFAULT_MAX_TOKENS)
                    if not isinstance(raw_max, int) or isinstance(raw_max, bool) or raw_max < 1:
                        raw_max = trusted.limit.max_tokens
                else:
                    raw_max = trusted.limit.max_tokens
                base_for_compare = {"maxFileTokens": raw_max,
                                    "skip": list(trusted.limit.skip), "files": dict(trusted.limit.files)}
                if trusted.committed_enabled:
                    base_for_compare["enabled"] = True
            else:
                base_for_compare = {"enabled": True, "maxFileTokens": trusted.limit.max_tokens,
                                    "skip": list(trusted.limit.skip), "files": dict(trusted.limit.files)}
            problem = sizes.refused_policy_change(base_for_compare, work_raw, trusted.limit.max_tokens)
            if problem:
                if problem == "size baseline cannot be read":
                    return [f"{baseline_rel}: {problem}"]
                return [f"size limit: {problem}"]
        limit = trusted.limit
    skip = tuple(sizes.DEFAULT_SKIP) + tuple(limit.skip) + tuple(_generated_list(contract)) + (baseline_rel,)
    limit = sizes.Limit(limit.max_tokens, skip, limit.files)
    try:
        current = _walk_sizes(root)
    except OSError as error:
        return [f"size limit: worktree cannot be read: {error}"]
    verdict = sizes.judge(current, limit)
    return [f"size limit: {sizes.format_over(item)}" for item in verdict.over]
