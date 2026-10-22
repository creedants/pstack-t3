from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

COMMAND_LINE_FLOOR = 7

FOLLOW_UP = "## Follow-ups\n\n- Correctness: File the skill line.\n"

FIXTURE_SKILL = """```bash
$B ticket add --summary "<request>" --kind feature|fix|polish|upkeep|study [--goal G<n>] --source user|github|<feed> [--ref <ref>] [--request A<n>] [--again] [--priority urgent|normal|low] [--paths src/a,src/b] [--decision]
$B ticket add --from-report reports/D2.md [--dry-run]
$B fire --tickets T1,T3 --station bug-fix --summary "<outcome>" --paths src/a,src/b [--timebox 60] [--mode full --reason "<one line>"] [--kind <kind>] [--ride-along T3]
$B fire --tickets ... --station <playbook> --summary "<outcome>" --timebox <minutes> --paths <files and directories the dish will change> [--branch <name>]
$B ticket add --from-report reports/D1.md
```"""

# Fixture for the regression tests in tests/test_skill_commands.py.

# Quoted "..." fills from "...quoted". A bare "..." fills from "...bare".
FILL = {
    "<request>": "Skill line request",
    "<outcome>": "Skill line outcome",
    "<one line>": "skill line",
    "<kind>": "fix",
    "<playbook>": "bug-fix",
    "<minutes>": "60",
    "<files and directories the dish will change>": "src/a",
    "<name>": "skill-line",
    "<dish>": "D1",
    "<ref>": "app-7f3",
    "<n>": "1",
    "...quoted": "File the follow-up",
    "...bare": "T1",
}
KIND_CHOICE = frozenset({"feature", "fix", "polish", "upkeep", "study"})
RESTAURANT = "Perf"
# open slugs --name. Report refs and the lease holder use that slug.
SLUG = "perf"
# open ignores --at and joins the store, the project directory's slug, and the restaurant slug.
# "Bridge Kit" is the directory BrigadeTest uses, so the opened path is {store}/bridge-kit/perf.
PROJECT_DIR = "Bridge Kit"
PROJECT_SLUG = "bridge-kit"
_PLACEHOLDER = re.compile(r"<[^>]*>")
_COMMAND = re.compile(r"^\$B (?:ticket add|fire)\b")

def skill_files() -> tuple[Path, ...]:
    override = os.environ.get("BRIGADE_SKILL_MD")
    if override:
        return (Path(override),)
    base = Path(__file__).resolve().parents[2] / "t3/added/brigade"
    return (base / "SKILL.md", *sorted((base / "reference").glob("*.md")))


def skill_text() -> str:
    """The skill markdown."""
    return skill_files()[0].read_text()


@dataclass(frozen=True)
class Span:
    """One command line as the scanner left it."""

    line: int
    text: str
    source: str = "SKILL.md"


@dataclass(frozen=True)
class Step:
    """One subprocess on a tape."""

    line: int
    argv: tuple[str, ...]
    expect: str | None
    source: str = "SKILL.md"


@dataclass(frozen=True)
class Tape:
    """Ordered argv one fresh store runs."""

    landing: bool
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class Token:
    """One word after the scan."""

    alternatives: tuple[str, ...]
    quoted: bool = False


