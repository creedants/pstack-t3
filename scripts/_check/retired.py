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
SCAN_TOPS = ("t3", "skills", "docs")
HISTORY = ("docs", "changelog")


def retired_tool_findings(root) -> list[str]:
    """One finding per retired T3 tool name per line under t3, skills, and docs, except docs/changelog.

    Each file is read as UTF-8 with errors replaced. Findings come in sorted path
    order, then line order, then column order, with the path relative to root in POSIX form.
    """
    base = Path(root)
    paths = []
    for top in SCAN_TOPS:
        for path in sorted((base / top).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            parts = path.relative_to(base).parts
            if len(parts) >= 2 and parts[0] == HISTORY[0] and parts[1] == HISTORY[1]:
                continue
            paths.append(path)
    paths.sort(key=lambda path: path.relative_to(base).as_posix())
    findings = []
    for path in paths:
        rel = path.relative_to(base).as_posix()
        text = path.read_bytes().decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            for match in RETIRED_TOOL.finditer(line):
                name = match.group(0)
                findings.append(
                    f"{rel}:{number}: retired T3 tool {name}; use {RETIRED_TOOLS[name]}: {line.strip()[:120]}"
                )
    return findings
