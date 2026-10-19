import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "t3/added/brigade/scripts/brigade.py"
SUPPORT = ROOT / "tests/brigade/support.py"
CODEX = "codex/gpt-6.1-sol"
CLAUDE = "claudeAgent/claude-opus-5-5"
# Thread ids the threads.tsv tests use. No real thread id appears in this repository.
THREAD_A = "mcp:00000000-0000-4000-8000-000000000001"
THREAD_B = "mcp:00000000-0000-4000-8000-000000000002"
THREAD_C = "mcp:00000000-0000-4000-8000-000000000003"
THREAD_D = "mcp:00000000-0000-4000-8000-000000000004"
HELD_QUESTION = "D1: grok could not pass the seat's options: refused. Move this coordinator to a provider that passes them?"
HELD_LINE = (
    "D1: open decision Q1: D1: grok could not pass the seat's options: refused. "
    "Move this coordinator to a provider that passes them?; "
    "launch no worker or verifier until 86 answer Q1"
)
STILL_OPEN = "Q1 still open; D1 stays held until 86 answer Q1 --answer moved"
ITEM_DECISION = (
    "brigade: an item decision's default must be one of its options, "
    "with at least one other option that closes it"
)
# The brigade reporting docs join in BRIGADE_DOCS order. Later items append to this tuple.
BRIGADE_DOCS = ("SKILL.md", "admin-reporting.md", "opening.md", "recovery.md", "script.md", "views.md")
# This repository's shared-document catalog and timebox, the value its own store would record.
DOC_SETTINGS = {
    "sharedDocs": [
        {"path": "README.md", "kind": "file"},
        {"path": "docs/guide.md", "kind": "file"},
        {"path": "t3/runtime.md", "kind": "file"},
        {"path": "t3/setup.md", "kind": "file"},
        {"path": "t3/added/", "kind": "skill"},
        {"path": "t3/overrides/", "kind": "skill"},
        {"path": "skills/", "kind": "skill"},
    ],
    "docTimebox": 90,
}


def brigade_docs():
    """The BRIGADE_DOCS members joined in order, read from the built skills tree."""
    return "\n".join((ROOT / "skills/brigade" / name).read_text() for name in BRIGADE_DOCS)


def _shown_root(path):
    """The project root walk prints, with the home directory as ~."""
    path = Path(path).resolve()
    try:
        relative = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    if not relative.parts:
        return "~"
    return "~/" + relative.as_posix()


def _owner_words(directory):
    """The --owner a coordinator reads from status, as the skill's service step 1 does."""
    try:
        meta = json.loads((Path(directory) / "restaurant.json").read_text())
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(meta, dict) or meta.get("generation") is None:
        return ()
    return ("--owner", f"{meta.get('thread') or ''}@{meta['generation']}")


LAND_SCRIPT = ROOT / "t3/added/landing/scripts/land.py"

def _land_has_rulings():
    """Change 8's share, lease reserve, and contest commands, which the admin's rulings run."""
    for words in (("share",), ("lease", "reserve"), ("contest",)):
        result = subprocess.run([sys.executable, str(LAND_SCRIPT), *words, "--help"], capture_output=True, text=True)
        if result.returncode != 0:
            return False
    return True

