"""Per-file token limit for agent-written files.

Tokens equal bytes divided by 4 with integer division. The committed
size-baseline.json names the allowance of each larger file. The contract points at that file with sizeBaseline and may set the maximum with sizeMaxTokens, which overrides the baseline maxFileTokens. The queue checks at land time.
"""

import fnmatch
import json
import posixpath
from dataclasses import dataclass, field
from enum import Enum


class Kind(Enum):
    CODE = "code"
    TEST = "test"
    TEXT = "text"


SPLIT_STEPS = {
    Kind.CODE: "code moves one responsibility to a sibling module",
    Kind.TEST: "tests start a new file for the feature and share helpers",
    Kind.TEXT: "skill and doc text moves rarely needed sections to an on-demand file with a pointer saying when to read it",
}

DEFAULT_BASELINE = "size-baseline.json"

DEFAULT_MAX_TOKENS = 5000

DEFAULT_SKIP = (
    "vendor/*",
    "__pycache__/*",
    "*.pyc",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.svg",
    "*.pdf",
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "go.sum",
)


class Read(Enum):
    WORKTREE = "worktree"
    INDEX = "index"
    COMMIT = "commit"


@dataclass(frozen=True)
class Relative:
    """An allowance measured from another file: its tokens plus this margin."""
    relative_to: str
    margin: int


@dataclass(frozen=True)
class Limit:
    max_tokens: int
    skip: tuple = ()
    files: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TrustedPolicy:
    enabled: bool
    limit: Limit
    error: object = None
    committed_enabled: bool = False


@dataclass(frozen=True)
class Over:
    path: str
    tokens: int
    allowed: object
    kind: Kind
    relative: object = None
    reference_tokens: object = None


@dataclass(frozen=True)
class Verdict:
    over: tuple = ()


def tokens_for(num_bytes):
    """Tokens for a byte count."""
    return num_bytes // 4


def kind_for(path):
    """Kind of one repository-relative path."""
    parts = path.split("/")
    if "tests" in parts or parts[-1].startswith("test"):
        return Kind.TEST
    if parts[-1].rsplit(".", 1)[-1].lower() in ("md", "txt", "rst"):
        return Kind.TEXT
    return Kind.CODE


def skipped(path, skip):
    """True when a skip entry covers this path."""
    for entry in skip:
        if fnmatch.fnmatchcase(path, entry):
            return True
        if entry.startswith("*."):
            if fnmatch.fnmatchcase(path.lower(), entry.lower()):
                return True
        if "*" not in entry and "?" not in entry and "[" not in entry:
            clean = entry.rstrip("/")
            if not clean:
                continue
            if path == clean or path.startswith(clean + "/"):
                return True
            if "/" not in clean:
                if path.split("/")[-1] == clean:
                    return True
    return False


def _reference_tokens(entry, reference_bytes):
    """Tokens of a relative entry's reference, or None when the reference is missing."""
    if reference_bytes is None:
        return None
    size = reference_bytes(entry.relative_to)
    return None if size is None else tokens_for(size)


def allowed_for(path, limit, reference_bytes=None):
    """Allowance of one path under this limit, or None when its reference is missing."""
    entry = limit.files.get(path, limit.max_tokens)
    if isinstance(entry, Relative):
        reference = _reference_tokens(entry, reference_bytes)
        return None if reference is None else reference + entry.margin
    return entry


def judge(sizes, limit, reference_bytes):
    """Verdict for a path to byte count mapping under this limit."""
    over = []
    for path in sorted(sizes):
        if skipped(path, limit.skip):
            continue
        tokens = tokens_for(sizes[path])
        entry = limit.files.get(path)
        allowed = allowed_for(path, limit, reference_bytes)
        if allowed is None:
            over.append(Over(path, tokens, None, kind_for(path), entry, None))
            continue
        if tokens <= allowed:
            continue
        if isinstance(entry, Relative):
            over.append(Over(path, tokens, allowed, kind_for(path), entry,
                             _reference_tokens(entry, reference_bytes)))
        else:
            over.append(Over(path, tokens, allowed, kind_for(path)))
    return Verdict(tuple(over))


def format_over(item):
    """One error line naming path, tokens, and allowance, then the split step."""
    if item.relative is None:
        return (f"{item.path}: {item.tokens} tokens over allowance {item.allowed} "
                f"for {item.kind.value}; {SPLIT_STEPS[item.kind]}")
    if item.allowed is None:
        return (f"{item.path}: {item.tokens} tokens over a relative allowance "
                f"for {item.kind.value}; reference {item.relative.relative_to} is missing; "
                f"{SPLIT_STEPS[item.kind]}")
    return (f"{item.path}: {item.tokens} tokens over allowance {item.allowed} "
            f"from reference {item.relative.relative_to} at {item.reference_tokens} tokens "
            f"plus margin {item.relative.margin} for {item.kind.value}; {SPLIT_STEPS[item.kind]}")


def _entry(path, value):
    """A parsed files entry: a token count or a Relative."""
    if isinstance(value, Relative):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, dict) and set(value) == {"relativeTo", "margin"}:
        reference = value["relativeTo"]
        margin = value["margin"]
        if (isinstance(reference, str) and reference and not reference.startswith("/")
                and ".." not in reference.split("/")
                and isinstance(margin, int) and not isinstance(margin, bool) and margin >= 0):
            normalized = posixpath.normpath(reference)
            if normalized != path:
                return Relative(normalized, margin)
    raise ValueError("files must map paths to token counts or relative allowances")


def format_verdict(verdict):
    """Every error line of this verdict, one per line."""
    return "\n".join(format_over(item) for item in verdict.over)


def parse_limit(obj):
    """Limit from a decoded baseline object."""
    if not isinstance(obj, dict):
        raise ValueError("baseline must be a JSON object")
    maximum = obj.get("maxFileTokens", DEFAULT_MAX_TOKENS)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise ValueError("maxFileTokens must be an integer at or above 1")
    skip = obj.get("skip", [])
    if not isinstance(skip, list) or any(not isinstance(entry, str) for entry in skip):
        raise ValueError("skip must be a list of strings")
    files = obj.get("files", {})
    if not isinstance(files, dict) or any(not isinstance(path, str) for path in files):
        raise ValueError("files must map paths to token counts or relative allowances")
    return Limit(maximum, tuple(skip), {path: _entry(path, value) for path, value in files.items()})


def baseline_enabled(obj):
    """True when a decoded baseline object explicitly opts in."""
    return isinstance(obj, dict) and obj.get("enabled") is True


def resolve_trusted(contract_maximum, base_raw, base_missing, base_error=None):
    """Trusted policy from the contract maximum and the trunk baseline.

    A contract maximum always enables the limit. Without one, only an
    explicitly enabled trunk baseline does. The effective maximum is the
    contract maximum when set, else the trunk maximum.
    """
    contract_on = (isinstance(contract_maximum, int) and not isinstance(contract_maximum, bool)
                   and contract_maximum >= 1)
    if contract_on:
        effective = contract_maximum
        if base_missing:
            return TrustedPolicy(True, Limit(effective, DEFAULT_SKIP, {}), None, False)
        if base_raw is None:
            detail = f": {base_error}" if base_error else ""
            return TrustedPolicy(True, Limit(effective, DEFAULT_SKIP, {}),
                                 f"size baseline at trunk tip cannot be read{detail}", False)
        if not isinstance(base_raw, dict):
            return TrustedPolicy(True, Limit(effective, DEFAULT_SKIP, {}),
                                 "size baseline at trunk tip cannot be read: baseline must be a JSON object",
                                 False)
        try:
            parsed = parse_limit(base_raw)
        except ValueError as error:
            return TrustedPolicy(True, Limit(effective, DEFAULT_SKIP, {}),
                                 f"size baseline at trunk tip cannot be read: {error}", False)
        return TrustedPolicy(True, Limit(effective, parsed.skip, parsed.files), None,
                             baseline_enabled(base_raw))
    if base_missing:
        return TrustedPolicy(False, Limit(DEFAULT_MAX_TOKENS, (), {}), None, False)
    if base_raw is None:
        return TrustedPolicy(False, Limit(DEFAULT_MAX_TOKENS, (), {}), None, False)
    if not isinstance(base_raw, dict):
        return TrustedPolicy(False, Limit(DEFAULT_MAX_TOKENS, (), {}), None, False)
    if not baseline_enabled(base_raw):
        return TrustedPolicy(False, Limit(DEFAULT_MAX_TOKENS, (), {}), None, False)
    try:
        parsed = parse_limit(base_raw)
    except ValueError as error:
        return TrustedPolicy(True, Limit(DEFAULT_MAX_TOKENS, (), {}),
                             f"size baseline at trunk tip cannot be read: {error}", True)
    return TrustedPolicy(True, parsed, None, True)


def refused_policy_change(base_raw, cand_raw, ceiling=None):
    """Why a candidate baseline weakens its base policy, or None. Lowering an allowance, the maximum, or the skip list in isolation always passes.

    Callers pass the effective maximum as ceiling. That is the contract maximum
    when one is set, else the base maximum. A new allowance above that ceiling
    is refused even when it sits at or below the base maximum. Effective
    allowances are compared over the union of base and candidate paths, so
    removing a below-maximum entry raises its allowance and is refused, while
    removing an above-maximum entry lowers it and stays legal.
    """
    if base_raw is None:
        if ceiling is None:
            return None
        if not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling < 1:
            return None
        base_raw = {"enabled": True, "maxFileTokens": ceiling, "skip": [], "files": {}}
    if not isinstance(base_raw, dict):
        return None
    if cand_raw is None:
        if base_raw.get("enabled") is True:
            return "size baseline removes enabled policy; a person merges that raise outside the queue"
        return None
    if not isinstance(cand_raw, dict):
        return "size baseline cannot be read"
    if base_raw.get("enabled") is True and cand_raw.get("enabled") is not True:
        return "size baseline removes enabled policy; a person merges that raise outside the queue"
    base_max = base_raw.get("maxFileTokens", DEFAULT_MAX_TOKENS)
    cand_max = cand_raw.get("maxFileTokens", DEFAULT_MAX_TOKENS)
    if isinstance(cand_max, bool) or not isinstance(cand_max, int) or cand_max < 1:
        return "size baseline cannot be read"
    if isinstance(base_max, int) and not isinstance(base_max, bool) and base_max >= 1:
        if cand_max > base_max:
            return "size baseline raises the maximum; a person merges that raise outside the queue"
    base_files = base_raw.get("files", {})
    cand_files = cand_raw.get("files", {})
    if not isinstance(base_files, dict):
        base_files = {}
    if not isinstance(cand_files, dict):
        return "size baseline cannot be read"
    if isinstance(ceiling, int) and not isinstance(ceiling, bool) and ceiling >= 1:
        base_ceiling = ceiling
    elif isinstance(base_max, int) and not isinstance(base_max, bool) and base_max >= 1:
        base_ceiling = base_max
    else:
        base_ceiling = DEFAULT_MAX_TOKENS
    for name, value in cand_files.items():
        if not isinstance(name, str):
            return "size baseline cannot be read"
        try:
            new = _entry(name, value)
        except ValueError:
            return "size baseline cannot be read"
        old = base_files.get(name)
        if old is not None:
            try:
                old = _entry(name, old)
            except ValueError:
                old = None
        if old is None:
            if isinstance(new, Relative):
                return f"size baseline adds a relative allowance for {name}; a person merges that raise outside the queue"
            if new <= base_ceiling:
                continue
            return f"size baseline adds a grandfathered file {name}; a person merges that raise outside the queue"
        if isinstance(old, Relative):
            if isinstance(new, int):
                return f"size baseline changes the relative allowance for {name} to a fixed one; a person merges that raise outside the queue"
            if new.relative_to != old.relative_to:
                return f"size baseline changes the reference for {name}; a person merges that raise outside the queue"
            if new.margin > old.margin:
                return f"size baseline raises the margin for {name}; a person merges that raise outside the queue"
            continue
        if isinstance(new, Relative):
            return f"size baseline changes the allowance for {name} to a relative one; a person merges that raise outside the queue"
        if new > old:
            return f"size baseline raises the allowance for {name}; a person merges that raise outside the queue"
    for name, value in base_files.items():
        if name in cand_files or not isinstance(name, str):
            continue
        try:
            old = _entry(name, value)
        except ValueError:
            continue
        if isinstance(old, Relative):
            return f"size baseline removes the relative allowance for {name}; a person merges that raise outside the queue"
        if base_ceiling > old:
            return f"size baseline raises the allowance for {name}; a person merges that raise outside the queue"
    base_skip = base_raw.get("skip", [])
    cand_skip = cand_raw.get("skip", [])
    if isinstance(base_skip, list) and isinstance(cand_skip, list):
        old_set = {entry for entry in base_skip if isinstance(entry, str)}
        for entry in cand_skip:
            if isinstance(entry, str) and entry not in old_set:
                return "size baseline widens skips; a person merges that raise outside the queue"
    return None


def load_baseline_file(path):
    """Limit from one baseline JSON file."""
    try:
        with open(path, encoding="utf-8") as handle:
            obj = json.load(handle)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not valid JSON: {error}") from error
    try:
        return parse_limit(obj)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
