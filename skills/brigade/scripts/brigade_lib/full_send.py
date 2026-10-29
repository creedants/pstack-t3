"""The full-send station: tstack's full-send method, read at the commit the repository's tstack.json pins.

The links name that commit literally, so scripts/check.py can compare them with tstack.json and
scripts/sync_tstack.py --bump can rewrite them. The seats here are the station's defaults. A full-send
item never resolves a seat through roles.py.
"""
import json

from brigade_lib.shared import BrigadeError

STATION = "full-send"
SKILLS = (
    ("full-send", "https://github.com/t3dotgg/tstack/blob/1085050f3baed1ac724caa4a8166145fb2ac88af/skills/full-send/SKILL.md"),
    ("sol-loop", "https://github.com/t3dotgg/tstack/blob/1085050f3baed1ac724caa4a8166145fb2ac88af/skills/sol-loop/SKILL.md"),
    ("file-pr", "https://github.com/t3dotgg/tstack/blob/1085050f3baed1ac724caa4a8166145fb2ac88af/skills/file-pr/SKILL.md"),
    ("babysit-pr", "https://github.com/t3dotgg/tstack/blob/1085050f3baed1ac724caa4a8166145fb2ac88af/skills/babysit-pr/SKILL.md"),
)
WRITER = {"instanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}}
SOL = {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "high"}}
TIMEBOX = ("never waives a full-send step (proof, a sol-loop round, or the PR draft). "
           "At the limit, write the report with what remains instead of skipping steps.")


def writer_selection(ident, live):
    """The writer's t3_thread_launch modelSelection as JSON. A cooling writer provider refuses, because the station has no backup seat."""
    cooling = [entry for entry in live if entry[0].partition("/")[0] == WRITER["instanceId"]]
    if cooling:
        seat, end, _ = cooling[0]
        raise BrigadeError(f"{ident}: {seat} cooling until {end}; a full-send item has no backup seat")
    return json.dumps(WRITER)


def head(ident, branch, reports):
    """The lines a full-send brief carries in place of the poteto-mode playbook line, its Mode lines, and the seat rule."""
    sol = f"{SOL['providerInstanceId']}/{SOL['model']}"
    return [
        "Use tstack's full-send method in place of a poteto-mode playbook, its architect step, and its code delegate.",
        "Gate: brigade", "",
        "FULL SEND:",
        "The owner asked for full send on this item. Read these four skills at this exact commit, never at another ref, "
        "and work the item with them:",
        *[f"- {name}: {link}" for name, link in SKILLS],
        "The SCOPE, CONTEXT, ACCEPTANCE, CONTRACT, VERIFY, STANDING RULES, REPORT, and STANDING ORDERS sections below "
        "apply, except where an adaptation says otherwise. These adaptations override the four skills where they conflict:",
        "1. Steps 1 to 3 run as written. For sol-loop, run each Sol round with `delegate_task`, mode \"async\", role "
        f"\"review\", target {json.dumps(SOL, separators=(',', ':'))}, and a read-only brief. Wait on each round per the "
        "REPORT section's child-task rules. Keep the sol-loop ledger and put it in your report. Your Sol rounds never "
        "count as the gate. The coordinator's independent review is the gate.",
        "2. Step 4 (file-pr). Do not open a PR. This repository lands every change through its landing queue, which "
        "opens the PR itself. Instead, write the PR title on the first line and the description below it, drafted per "
        f"file-pr, to {reports / f'{ident}-pr.md'}. "
        "End the description with \"Coordinated by <the coordinator's provider/model>. Written by <your provider/model>. "
        f"Reviewed by {sol}.\" in place of any \"Created with\" line. Keep the one line that says how you proved the "
        "change. Keep history linear. The landing queue lands your branch on trunk.",
        "3. Steps 5 and 6 (babysit-pr and merge) are done by the coordinator through the landing queue after an "
        f"independent review. Skip them. Push nothing except your own branch {branch}, and force-push only that branch "
        "with --force-with-lease after a rebase. Never merge.",
        "4. Step 7. Your report is the REPORT section's file, not a final chat message. Include the proof for every "
        "change in the diff, the sol-loop ledger (rounds, severities, outcomes), each decision you made, and the wall "
        "time from start to report.",
        "5. Asking the user. Do not use a question tool. If you reach a point the full-send skill says to ask, write the "
        "report with status BLOCKED and the question with your recommended option first, then send the done message as "
        "the REPORT section says.",
        "6. Testing permission does not lift any rule in the CONTEXT or STANDING ORDERS sections or the scratch rules.",
    ]
