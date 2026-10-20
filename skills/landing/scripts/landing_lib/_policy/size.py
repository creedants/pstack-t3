import json
import os
import stat
from pathlib import Path
from landing_lib import seam
from .contract import repo_root

def _tracked_names(repo, *args):
    out = seam.git("ls-files", "-z", *args, cwd=repo, exact=True).stdout
    return [path for path in out.split("\0") if path]

def _diff_names(repo, *args):
    out = seam.git("diff", "--name-only", "-z", "--no-renames", *args, cwd=repo, exact=True).stdout
    return [path for path in out.split("\0") if path]

def _worktree_sizes(root, names):
    """Byte counts of the names that are regular files, following symlinks, in this worktree."""
    sizes = {}
    for path in names:
        try:
            kind = os.stat(Path(root) / path)
        except OSError:
            continue
        if stat.S_ISREG(kind.st_mode):
            sizes[path] = kind.st_size
    return sizes

def _all_worktree_names(repo):
    return _tracked_names(repo) + _tracked_names(repo, "--others", "--exclude-standard")

def _index_sizes(repo, names):
    """Byte counts of the names as staged in the index."""
    sizes = {}
    for path in names:
        sized = seam.git("cat-file", "-s", f":0:{path}", cwd=repo, check=False)
        if sized.returncode != 0:
            continue
        try:
            sizes[path] = int(sized.stdout.strip())
        except ValueError:
            continue
    return sizes

def _commit_sizes(repo, commit, names, skip):
    """Byte counts of the names as stored in one commit."""
    sizes = {}
    for path in names:
        if seam.skipped(path, skip):
            continue
        sized = seam.git("cat-file", "-s", f"{commit}:{path}", cwd=repo, check=False)
        if sized.returncode != 0:
            continue
        try:
            sizes[path] = int(sized.stdout.strip())
        except ValueError:
            continue
    return sizes

def _worktree_reference(root):
    """Reader of worktree reference sizes. A path that is not a regular file reads as None."""
    def read(path):
        try:
            kind = os.stat(Path(root) / path)
        except OSError:
            return None
        return kind.st_size if stat.S_ISREG(kind.st_mode) else None
    return read

def _index_reference(repo):
    """Reader of staged reference sizes. A path that is not a regular file at stage 0 reads as None."""
    def read(path):
        listed = seam.git("ls-files", "-s", "-z", "--", f":(literal){path}", cwd=repo, check=False, exact=True)
        if listed.returncode != 0:
            return None
        records = [record for record in listed.stdout.split("\0") if record]
        if len(records) != 1:
            return None
        meta, _, name = records[0].partition("\t")
        fields = meta.split()
        if name != path or len(fields) != 3:
            return None
        mode, _, stage = fields
        if mode not in ("100644", "100755") or stage != "0":
            return None
        sized = seam.git("cat-file", "-s", f":0:{path}", cwd=repo, check=False)
        if sized.returncode != 0:
            return None
        try:
            return int(sized.stdout.strip())
        except ValueError:
            return None
    return read

def _commit_reference(repo, commit):
    """Reader of reference sizes as stored in one commit. A path that is not a regular file reads as None."""
    def read(path):
        listed = seam.git("ls-tree", "-l", "-z", commit, "--", f":(literal){path}", cwd=repo, check=False, exact=True)
        if listed.returncode != 0:
            return None
        records = [record for record in listed.stdout.split("\0") if record]
        if len(records) != 1:
            return None
        meta, _, name = records[0].partition("\t")
        fields = meta.split()
        if name != path or len(fields) != 4:
            return None
        mode, kind, _, size = fields
        if mode not in ("100644", "100755") or kind != "blob":
            return None
        try:
            return int(size)
        except ValueError:
            return None
    return read

def _write_baseline(path, maximum, skip, files, enabled=None):
    payload = {"maxFileTokens": maximum, "skip": list(skip), "files": dict(files)}
    if enabled is True:
        payload["enabled"] = True
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def _read_worktree_baseline_raw(baseline_path):
    try:
        return json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None

def _show_baseline_raw(repo, commit, baseline_rel):
    shown = seam.git("show", f"{commit}:{baseline_rel}", cwd=repo, check=False)
    if shown.returncode != 0:
        return None, True
    try:
        return json.loads(shown.stdout), False
    except json.JSONDecodeError:
        return None, False

def seed_baseline(path, maximum, sizes, skip=None):
    """Write a baseline naming each counted over-limit file at its current tokens."""
    if skip is None:
        skip = seam.DEFAULT_SKIP
    over = {name: seam.tokens_for(count) for name, count in sorted(sizes.items())
            if not seam.skipped(name, skip) and seam.tokens_for(count) > maximum}
    _write_baseline(path, maximum, skip, over)

def shrink_baseline(path, sizes, maximum=None):
    """Lower stored allowances that fell. Never raises one. Drops entries whose stored allowance sat above the maximum once the file fits. A relative entry is left as it is."""
    stored = seam.load_baseline_file(str(path))
    raw = _read_worktree_baseline_raw(path)
    kept = raw.get("enabled") is True if isinstance(raw, dict) else None
    raw_files = raw.get("files") if isinstance(raw, dict) else None
    files = dict(raw_files) if isinstance(raw_files, dict) else dict(stored.files)
    effective = maximum if isinstance(maximum, int) and not isinstance(maximum, bool) and maximum >= 1 else stored.max_tokens
    for name in list(files):
        stored_tokens = files[name]
        if isinstance(stored_tokens, bool) or not isinstance(stored_tokens, int):
            continue
        if name not in sizes:
            continue
        tokens = seam.tokens_for(sizes[name])
        if tokens < stored_tokens:
            if stored_tokens > effective and tokens <= effective:
                del files[name]
            else:
                files[name] = tokens
    _write_baseline(path, stored.max_tokens, stored.skip, files, kept)

def _size_contract(path):
    try:
        with seam.Store.for_repo(path) as store:
            return dict(store.contract)
    except seam.LandError:
        return {}

def _index_baseline_broken(repo, baseline_rel):
    """True when the index holds a baseline file that does not parse."""
    try:
        shown = seam.git("show", f":0:{baseline_rel}", cwd=repo, check=False)
    except seam.LandError:
        return False
    if shown is None or shown.returncode != 0:
        return False
    try:
        json.loads(shown.stdout)
    except json.JSONDecodeError:
        return True
    return False

def _head_names(repo):
    out = seam.git("ls-tree", "-r", "--name-only", "-z", "HEAD", cwd=repo, check=False, exact=True).stdout
    return [path for path in out.split("\0") if path]

def _head_sizes(repo, skip=()):
    names = _head_names(repo)
    return _commit_sizes(repo, "HEAD", names, skip)

def _compare_base(trusted, base_raw, base_missing):
    """Policy a candidate baseline is compared against. Callers pass the trunk base or HEAD. trusted is enabled with no error."""
    maximum = trusted.limit.max_tokens if base_missing else base_raw.get("maxFileTokens", seam.DEFAULT_MAX_TOKENS)
    base = {"maxFileTokens": maximum, "skip": list(trusted.limit.skip), "files": dict(trusted.limit.files)}
    if trusted.committed_enabled:
        base["enabled"] = True
    return base

def size_report(path, staged, all_files, shrink, baseline):
    """Judge repo files and raise LandError for size violations."""
    repo = str(repo_root(path))
    contract = _size_contract(path)
    generated = list(contract.get("generated") or [])
    baseline_rel = baseline or contract.get("sizeBaseline") or seam.DEFAULT_BASELINE
    baseline_path = Path(baseline_rel) if Path(baseline_rel).is_absolute() else Path(repo) / baseline_rel
    if shrink and not baseline_path.is_file():
        raise seam.LandError(f"no baseline at {baseline_rel}; run land.py size without --shrink to seed one")
    if not baseline_path.is_file():
        maximum = contract.get("sizeMaxTokens") or seam.DEFAULT_MAX_TOKENS
        seed_skip = tuple(seam.DEFAULT_SKIP) + tuple(generated) + (baseline_rel,)
        if staged or contract.get("sizeMaxTokens") is not None:
            seed_baseline(baseline_path, maximum, _head_sizes(repo), seed_skip)
        else:
            seed_baseline(baseline_path, maximum, _worktree_sizes(repo, _all_worktree_names(repo)), seed_skip)
    try:
        stored = seam.load_baseline_file(str(baseline_path))
    except ValueError as error:
        # The staged path reports the index failure when HEAD holds a baseline and both copies do not parse.
        if staged:
            _, head_missing = _show_baseline_raw(repo, "HEAD", baseline_rel)
            if not head_missing and _index_baseline_broken(repo, baseline_rel):
                raise seam.LandError("size baseline cannot be read") from error
        raise seam.LandError(str(error)) from error
    if shrink:
        maximum = contract.get("sizeMaxTokens") or stored.max_tokens
        shrink_baseline(baseline_path, _worktree_sizes(repo, _all_worktree_names(repo)), maximum)
        try:
            stored = seam.load_baseline_file(str(baseline_path))
        except ValueError as error:
            raise seam.LandError(str(error)) from error
    maximum = contract.get("sizeMaxTokens") or stored.max_tokens
    if staged:
        head_raw, head_missing = _show_baseline_raw(repo, "HEAD", baseline_rel)
        contract_max = contract.get("sizeMaxTokens")
        trusted = seam.resolve_trusted(contract_max, None if head_missing else head_raw, head_missing, None)
        if trusted.error:
            raise seam.LandError(trusted.error)
        if trusted.enabled:
            try:
                index_raw_text = seam.git("show", f":0:{baseline_rel}", cwd=repo, check=False)
            except seam.LandError:
                index_raw_text = None
            if index_raw_text is not None and index_raw_text.returncode == 0:
                try:
                    index_raw = json.loads(index_raw_text.stdout)
                except json.JSONDecodeError:
                    raise seam.LandError("size baseline cannot be read")
            else:
                index_raw = None
            if index_raw is None:
                if not head_missing:
                    raise seam.LandError("size baseline removes enabled policy; a person merges that raise outside the queue")
            else:
                problem = seam.refused_policy_change(_compare_base(trusted, head_raw, head_missing), index_raw, trusted.limit.max_tokens)
                if problem:
                    raise seam.LandError(problem)
            skip = tuple(seam.DEFAULT_SKIP) + tuple(trusted.limit.skip) + tuple(generated) + (baseline_rel,)
            limit = seam.Limit(trusted.limit.max_tokens, skip, dict(trusted.limit.files))
        elif not head_missing:
            try:
                index_raw_text = seam.git("show", f":0:{baseline_rel}", cwd=repo, check=False)
            except seam.LandError:
                index_raw_text = None
            index_raw = None
            if index_raw_text is not None and index_raw_text.returncode == 0:
                try:
                    index_raw = json.loads(index_raw_text.stdout)
                except json.JSONDecodeError:
                    raise seam.LandError("size baseline cannot be read")
            try:
                head_limit = seam.parse_limit(head_raw) if isinstance(head_raw, dict) else stored
            except ValueError as error:
                raise seam.LandError(str(error)) from error
            effective = contract.get("sizeMaxTokens") or head_limit.max_tokens
            problem = seam.refused_policy_change(head_raw, index_raw, effective)
            if problem:
                raise seam.LandError(problem)
            head_max = effective
            skip = tuple(seam.DEFAULT_SKIP) + tuple(head_limit.skip) + tuple(generated) + (baseline_rel,)
            limit = seam.Limit(head_max, skip, dict(head_limit.files))
        else:
            skip = tuple(seam.DEFAULT_SKIP) + tuple(stored.skip) + tuple(generated) + (baseline_rel,)
            limit = seam.Limit(maximum, skip, dict(stored.files))
        names = _diff_names(repo, "--cached", "HEAD")
        sizes = _index_sizes(repo, names)
    else:
        skip = tuple(seam.DEFAULT_SKIP) + tuple(stored.skip) + tuple(generated) + (baseline_rel,)
        limit = seam.Limit(maximum, skip, dict(stored.files))
        if all_files:
            sizes = _worktree_sizes(repo, _all_worktree_names(repo))
        else:
            names = _diff_names(repo, "HEAD") + _tracked_names(repo, "--others", "--exclude-standard")
            sizes = _worktree_sizes(repo, names)
    verdict = seam.judge(sizes, limit, _index_reference(repo) if staged else _worktree_reference(repo))
    if verdict.over:
        raise seam.LandError(seam.format_verdict(verdict))
    return f"size ok: checked {len(sizes)} paths via {baseline_rel}"

def check_candidate_size(store, base, candidate):
    """Why the candidate fails the size limit, or None.

    The check runs at land time, not at submit time, so the rebased change is
    what is judged. The limit comes from the contract maximum, or from the baseline file committed at the
    trunk tip, read with git show. The contract points at that file with sizeBaseline and may set the maximum with sizeMaxTokens, which overrides the baseline maxFileTokens. A human raise goes through a reviewed PR that a person merges outside the queue.
    """
    contract = store.contract
    maximum = contract.get("sizeMaxTokens")
    baseline_rel = contract.get("sizeBaseline") or seam.DEFAULT_BASELINE
    repo = store.repo
    shown = seam.git("show", f"{base}:{baseline_rel}", cwd=repo, check=False)
    if shown.returncode != 0:
        base_missing = True
        base_raw = None
        base_error = None
    else:
        base_missing = False
        try:
            base_raw = json.loads(shown.stdout)
        except json.JSONDecodeError as error:
            base_raw = None
            base_error = str(error)
        else:
            base_error = None
    trusted = seam.resolve_trusted(maximum, base_raw, base_missing, base_error)
    if trusted.error:
        return trusted.error
    if not trusted.enabled:
        return None
    cand_shown = seam.git("show", f"{candidate}:{baseline_rel}", cwd=repo, check=False)
    if cand_shown.returncode == 0:
        try:
            cand_obj = json.loads(cand_shown.stdout)
        except json.JSONDecodeError:
            return "size baseline cannot be read"
        problem = seam.refused_policy_change(_compare_base(trusted, base_raw, base_missing), cand_obj, trusted.limit.max_tokens)
        if problem:
            return problem
    elif not base_missing:
        return "size baseline removes enabled policy; a person merges that raise outside the queue"
    limit = trusted.limit
    generated = list(contract.get("generated") or [])
    limit = seam.Limit(limit.max_tokens, tuple(seam.DEFAULT_SKIP) + tuple(limit.skip) + tuple(generated) + (baseline_rel,), limit.files)
    changed = seam.changed_paths(repo, base, candidate)
    sizes = _commit_sizes(repo, candidate, changed, limit.skip)
    verdict = seam.judge(sizes, limit, _commit_reference(repo, candidate))
    if verdict.over:
        return seam.format_verdict(verdict)
    return None
