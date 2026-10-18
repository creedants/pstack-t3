"""Per-file token limit for agent-written files.

Tokens equal bytes divided by 4 with integer division. The committed
size-baseline.json names the allowance of each larger file. The contract points at that file with sizeBaseline and may set the maximum with sizeMaxTokens, which overrides the baseline maxFileTokens. The queue checks at land time.
"""

import fnmatch
import json
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
class Limit:
    max_tokens: int
    skip: tuple = ()
    files: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Over:
    path: str
    tokens: int
    allowed: int
    kind: Kind


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


def allowed_for(path, limit):
    """Allowance of one path under this limit."""
    return limit.files.get(path, limit.max_tokens)


def judge(sizes, limit):
    """Verdict for a path to byte count mapping under this limit."""
    over = []
    for path in sorted(sizes):
        if skipped(path, limit.skip):
            continue
        tokens = tokens_for(sizes[path])
        allowed = allowed_for(path, limit)
        if tokens > allowed:
            over.append(Over(path, tokens, allowed, kind_for(path)))
    return Verdict(tuple(over))


def format_over(item):
    """One error line naming path, tokens, and allowance, then the split step."""
    return (f"{item.path}: {item.tokens} tokens over allowance {item.allowed} "
            f"for {item.kind.value}; {SPLIT_STEPS[item.kind]}")


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
    if (not isinstance(files, dict) or
            any(not isinstance(path, str) for path in files) or
            any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in files.values())):
        raise ValueError("files must map paths to token counts at or above 0")
    return Limit(maximum, tuple(skip), dict(files))


def baseline_enabled(obj):
    """True when a decoded baseline object explicitly opts in."""
    return isinstance(obj, dict) and obj.get("enabled") is True


def refused_policy_change(base_raw, cand_raw, ceiling=None):
    """Why a candidate baseline weakens its base policy, or None. Lowering always passes.

    Callers pass the effective maximum as ceiling. That is the contract maximum
    when one is set, else the base maximum. A new allowance above that ceiling
    is refused even when it sits at or below the base maximum.
    """
    if base_raw is None:
        return None
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
    if isinstance(base_max, int) and not isinstance(base_max, bool) and base_max >= 1:
        base_ceiling = base_max
    else:
        base_ceiling = DEFAULT_MAX_TOKENS
    if isinstance(ceiling, int) and not isinstance(ceiling, bool) and ceiling >= 1:
        base_ceiling = min(base_ceiling, ceiling)
    for name, value in cand_files.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            continue
        old = base_files.get(name)
        if old is None:
            if value <= base_ceiling:
                continue
            return f"size baseline adds a grandfathered file {name}; a person merges that raise outside the queue"
        if isinstance(old, int) and not isinstance(old, bool) and value > old:
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
