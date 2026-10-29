import re
from pathlib import Path


RETIRED_TOOLS: dict[str, str] = {
    "update_scheduled_task": "schedule_task with scheduledTaskId",
    **{f"preview_{a}": "preview_act with action" for a in
       ("click", "type", "hover", "select", "drag", "upload", "press", "scroll")},
    **{f"t3_queue_{a}": "t3_queue with action" for a in
       ("list", "read", "edit", "cancel", "reorder", "promote_to_steer")},
    **{f"t3_pending_request_{a}": "t3_pending_request with action" for a in ("list", "read", "respond")},
    **{f"t3_thread_{a}": "t3_thread_transfer with action" for a in ("fork", "merge_back", "transfers")},
    "t3_thread_configuration": "t3_thread_configure with no modelSelection",
}
RETIRED_TOOL = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(sorted(RETIRED_TOOLS, key=len, reverse=True)) + r")(?![A-Za-z0-9_])"
)
# The tree handed to check_tree holds the skills. The checkout adds the sources that render into it
# and the hand-written docs beside it.
SOURCE_TOPS = ("t3", "docs")
# History keeps the names its release shipped. docs/cli is generated from t3/scripts, which the
# checkout scan reads directly, and the build writes that page after its own check runs.
SKIPPED = (("docs", "changelog"), ("docs", "cli"))


def _files(base, tops, skipped):
    """Every file below one of tops under base, minus the skipped two-part prefixes and __pycache__."""
    paths = []
    for top in tops:
        for path in sorted((base / top).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            parts = path.relative_to(base).parts
            if any(parts[:2] == prefix for prefix in skipped):
                continue
            paths.append(path)
    return paths


def retired_tool_findings(tree, repo=None) -> list[str]:
    """One finding per retired T3 tool name per line in the given skills tree, and in the checkout.

    The tree scan covers every file it holds. When repo is given, the checkout's t3 and docs trees
    are read too, minus docs/changelog and docs/cli. A tree finding names the path relative to
    tree, a checkout finding the path relative to repo, which is how the other check_tree findings
    read. Each file is read as UTF-8 with errors replaced. Each scan comes in sorted path order,
    then line order, then column order.
    """
    scans = [(Path(tree), (".",), ())]
    if repo is not None:
        scans.append((Path(repo), SOURCE_TOPS, SKIPPED))
    findings = []
    for base, tops, skipped in scans:
        for path in _files(base, tops, skipped):
            rel = path.relative_to(base).as_posix()
            text = path.read_bytes().decode("utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                for match in RETIRED_TOOL.finditer(line):
                    name = match.group(0)
                    findings.append(
                        f"{rel}:{number}: retired T3 tool {name}; use {RETIRED_TOOLS[name]}: {line.strip()[:120]}"
                    )
    return findings
