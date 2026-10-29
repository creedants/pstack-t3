import dataclasses
import re
from pathlib import Path
from typing import Any


# Each pair is a pattern and the reason in the finding.
FORBIDDEN = [
    (r"subagent_type", "Cursor Task schema; use delegate_task with role and target"),
    (r"environment: ?\"(?:cloud|local)\"", "Cursor cloud agents; T3 children run locally"),
    (r"cloud_base_branch", "Cursor cloud agents; name the branch in the brief"),
    (r"run_in_background", "Cursor Task flag; use delegate_task mode async"),
    (r"`Task`|Task tool|Task call|Task subagent|Task `model`", "Cursor Task tool; use delegate_task"),
    (r"pstack-models\.mdc|\.cursor/rules", "Cursor rule file; use roles.py and roles.json"),
    (r"(?<![\w/])/loop\b", "Cursor /loop; use schedule_task"),
    (r"AskQuestion", "Cursor question tool; use the host's question tool"),
    (r"cursor-team-kit|Cursor's built-in", "Cursor plugin dependency"),
    (r"agent-transcripts", "Cursor transcript store; use t3_thread_search and t3_thread_read"),
    (r"inherit-parent", "Cursor alias; use inherit"),
    (r"\b(?:claude-opus-5-5-max|claude-opus-5-5-xhigh|claude-fable-5-1-thinking-\w+|gpt-5\.6-sol-max|grok-4\.7-xhigh-fast|claude-opus-5-thinking-\w+)\b", "hard-coded Cursor model slug; resolve a role"),
    (r"cursor\.com/agents|cloud agent", "Cursor cloud agents; T3 child tasks or threads"),
    (
        r"(?i)\b(?:pick|choose|select|raise|lower|change|use|set)\b"
        r"(?:(?![.!?]).){0,180}?"
        r"(?:reasoning level|effort level|fast or standard speed|fast mode|standard speed)"
        r"|\b(?:pick|choose|select)\b(?:(?![.!?]).){0,60}?\beffort\b",
        "pick, choose, select, raise, lower, change, use, or set has at most 180 intervening characters before reasoning level, effort level, fast or standard speed, fast mode, or standard speed, "
        "or pick, choose, or select has at most 60 intervening characters before effort; both matches are case-insensitive, stay on one line, and have no '.', '!', or '?' between the verb and dial; "
        "point at the Subagents section through an override",
    ),
]
FRONTMATTER_REQUIRED = ("name", "description")
# The setup gate's entry in REQUIRED_TOOLS, whose gate string names the minimum T3 Code version.
SETUP_GATE = "setup-pstack/SKILL.md"
# Generated files that teach a PR wait must name T3's watch tools. Absent files are not checked.
REQUIRED_TOOLS = {
    "pstack-runtime/SKILL.md": ("watch_pull_request", "unwatch_pull_request"),
    "landing/SKILL.md": ("watch_pull_request", "unwatch_pull_request"),
    "brigade/SKILL.md": ("watch_pull_request", "unwatch_pull_request"),
    "poteto-mode/playbooks/babysit.md": ("watch_pull_request",),
    "poteto-mode/playbooks/shipping.md": ("watch_pull_request",),
    "poteto-mode/playbooks/autonomous-run.md": ("watch_pull_request",),
    "poteto-mode/playbooks/orchestrate.md": ("watch_pull_request",),
    "poteto-mode/playbooks/autopilot-full.md": ("watch_pull_request",),
    "poteto-mode/playbooks/autopilot-stack.md": ("watch_pull_request",),
    SETUP_GATE: ("watch_pull_request", "T3 Code 0.1.3 or later"),
}
LINK = re.compile(r"\]\(((?!https?:|mailto:|#)[^)\s]+)\)")
# A T3 thread link is `t3-thread://v1/<threadId>` or `<t3-thread://v2/<environmentId>/<threadId>>`. It names no file.
THREAD_LINK = re.compile(r"t3-thread://v[12]/[^)\s]")
# T3 Code 0.1.3 replaced each name here with the merged tool the value names. Keyed by the retired
# name, so the table is the one place a name is added and the reason is derived from it.
RETIRED_TOOLS = {
    "update_scheduled_task": "schedule_task with scheduledTaskId",
    **{f"preview_{action}": "preview_act with action" for action in
       ("click", "type", "hover", "select", "drag", "upload", "press", "scroll")},
    **{f"t3_queue_{action}": "t3_queue with action" for action in
       ("list", "read", "edit", "cancel", "reorder", "promote_to_steer")},
    **{f"t3_pending_request_{action}": "t3_pending_request with action" for action in ("list", "read", "respond")},
    **{f"t3_thread_{action}": "t3_thread_transfer with action" for action in ("fork", "merge_back", "transfers")},
    "t3_thread_configuration": "t3_thread_configure with no modelSelection",
}
# A retired name is bounded by anything but a letter or digit, so `mcp__t3-code__preview_click` matches
# and `t3_thread_transfer` does not.
RETIRED_TOOL = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(sorted(RETIRED_TOOLS, key=len, reverse=True)) + r")(?![A-Za-z0-9_])"
)
# The checkout scan adds the sources that render into the judged tree and the hand-written docs beside it.
RETIRED_SOURCE_TOPS = ("t3", "docs")
# History keeps the names its release shipped. docs/cli is generated from t3/scripts, which the
# checkout scan reads directly, and the build writes that page after its own check runs.
RETIRED_SKIPPED = (("docs", "changelog"), ("docs", "cli"))
# Bash ends a <<'JSON' body only on a line that is JSON and nothing else.
CATALOG_HEREDOC_OPEN = "<<'JSON'"
CATALOG_HEREDOC_CLOSER = "JSON"
RENDERED_MARKER = Path("pstack-runtime/scripts/roles.py")

ROLES_PY = Path(__file__).resolve().parents[2] / "t3/scripts/roles.py"
TESTS_DIR = Path(__file__).resolve().parents[2] / "tests"
README_PATH = Path(__file__).resolve().parents[2] / "README.md"
UPSTREAM_PATH = Path(__file__).resolve().parents[2] / "upstream.json"
GUIDE_PATH = Path(__file__).resolve().parents[2] / "docs/guide.md"


@dataclasses.dataclass(frozen=True)
class Layout:
    """Repo paths and yaml, built at call time so patched facade globals stay visible."""

    repo: Path
    roles_py: Path
    tests_dir: Path
    readme_path: Path
    upstream_path: Path
    guide_path: Path
    vendor_skills_path: Path
    yaml: Any
