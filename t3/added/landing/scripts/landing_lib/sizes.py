"""Per-file token limit for agent-written files.

Tokens equal bytes divided by 4 with integer division. The committed
size-baseline.json names the allowance of each larger file. The landing
contract only points at that file. The queue checks at land time.
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
    "skills/*",
    "docs/cli/*",
    "docs/skills.md",
    "vendor/*",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.pdf",
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
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
        if "/" not in entry and "*" not in entry and "?" not in entry and "[" not in entry:
            if path == entry or path.startswith(entry.rstrip("/") + "/"):
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
