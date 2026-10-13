import argparse
import fcntl
import fnmatch
import functools
import hashlib
import io
import json
import os
import posixpath
import re
import runpy
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from brigade_lib import seam
from brigade_lib.items import attempt_kind, latest_mode_note, menu_purpose, record_escalation
from brigade_lib.reviews import fix_round_refusal, item_verdicts, send_backs
from brigade_lib.shared import BrigadeError, slug
from brigade_lib.store import HELD_LOCKS, MODES, VERDICTS, roles_script



MEASURING_STATIONS = ("perf-issue", "hillclimb", "eval")
TEST_STANDARD = (
    "1. A test earns its place when it fails for a defect that no other check reports; name that defect in the test name.",
    "2. A test that pins a reviewed defect stays; remove it only with a mutation that shows another check fails for the same change.",
    "3. Use a rule in the project's static check, when it has one, when the property is visible in the text or the tree without running the code, with one passing and one failing fixture test.",
    "4. Use a test when the property is behavior: exit codes, written files, ordering, refusals.",
    "5. Call the command in the test process through the project's in-process test helper, when it has one; start a process only for a race, a signal, exit status, the environment, or help output, and say which.",
    "6. Never assert a library's own wording.",
    "7. Add a case to an existing table when the setup is the same.",
    "8. A test that sleeps to order two processes names the review finding it pins.",
    "9. Do not add a test for what the project's build already refuses.",
    "10. The reviewer asks of each new test: which change to the code makes this fail and nothing else fail?",
)
ESCALATED = "escalated: "
MODE_RESOLUTIONS = 3
SEAT_RULE = ("Seat rule. Copy the Mode value above into --brief-mode on every roles.py mode and roles.py show call you make, "
             "and pass no other mode flag. Never pass --session-mode. Mode source names where your launcher's decision came "
             "from. It does not make this thread a session.")
WAIT_RULE = ("- Never end your turn while a child task you started with delegate_task is still open, per step 5 of the "
             "pstack-runtime skill's Delegation section. This overrides delegate_task's text that says to end the turn and "
             "wait for a notification. A completion wakes you only when the child's run ends, and a stalled child or a run "
             "left open never ends. Wait on each open child task with t3_thread_wait on its childThreadId and timeoutMs "
             "300000, then read it with task_status. A child whose run is still open and whose last message holds the result "
             "is stalled after two minutes with no new activity, per step 5. If that wait returns at once while workState is "
             "still working or waiting_for_children, the child's run ended with its task open. Then wait between task_status "
             "checks with t3_thread_wait and timeoutMs 120000 on your own thread, the parentThreadId from "
             "orchestrator_capabilities, with runId set to its activeRunId from t3_thread_read, never a shell sleep, and "
             "count ten minutes from its last activity item. "
             "A child task is open while its workState is working or "
             "waiting_for_children, whatever hasPendingChildRuns says. Cancel a child task with task_cancel when it runs past "
             "its budget or stalls, per the runtime's Failure handling. A thread you launched with t3_thread_launch has no "
             "parent, so its finished turn never wakes you. While it stays healthy, repeat t3_thread_wait on its threadId with "
             "timeoutMs 300000 and read its activity with t3_thread_read. A timeout alone never stops it. Stop it with "
             "t3_thread_interrupt and then a terminal t3_thread_wait only when it stalls, with no new activity item for ten "
             "minutes per step 5 of the runtime's Delegation section, or runs past its budget. A long-lived "
             "owner your playbook supervises, such as an Orchestrate PR owner, follows the runtime's Top-level threads section "
             "and its playbook instead, and does not hold your report. Write the report only once every child task and every "
             "other thread you launched is terminal.")


@dataclass(frozen=True)
class ModeInputs:
    project_root: str
    station: str
    attempt: str
    paths: str
    send_backs: int
    coordinator: str
    escalated: str
    dish: dict
    thread: str
    generation: int


@dataclass(frozen=True)
class ModeLines:
    lines: tuple
    mode: str
    escalation: str


def brief_dish(restaurant, ident, paths, lease, acceptance):
    _, dish = restaurant.find("dishes.tsv", ident)
    refusal = fix_round_refusal(restaurant, dish)
    if refusal:
        raise BrigadeError(refusal)
    paths, lease = paths or dish.get("paths", ""), lease or dish.get("lease", "")
    if not paths or not lease:
        raise BrigadeError(f"{ident} has no lease; fire with --paths, or pass --paths and --lease")
    if not acceptance:
        raise BrigadeError("a brief needs at least one --acceptance criterion")
    if dish["state"] not in ("in-progress", "sent-back"):
        raise BrigadeError(f"{ident} is {dish['state']}; brief a dish that is in progress or sent back")
    thread = (restaurant.meta.get("thread") or "").strip()
    if not thread:
        raise BrigadeError("no coordinator thread recorded; run brigade.py set --thread")
    menu_purpose(restaurant)
    return dish, paths, lease, thread


def mode_inputs(restaurant, dish, paths, thread):
    meta = restaurant.meta
    events = restaurant.rows("log.tsv")
    sent_back = len(send_backs(restaurant.rows("pass.tsv"), dish["id"]))
    return ModeInputs(meta["projectRoot"], dish["station"], attempt_kind(events, dish["id"]), paths, sent_back,
                      meta.get("mode") if meta.get("mode") in MODES else None, latest_mode_note(events, dish["id"]),
                      dish, thread, meta.get("generation"))


def roles_mode(inputs):
    if HELD_LOCKS:
        raise BrigadeError(f"roles.py may run git; release {HELD_LOCKS[0]}'s store lock before resolving the mode")
    argv = [sys.executable, str(roles_script()), "mode", "--cwd", inputs.project_root, "--paths", inputs.paths,
            "--send-backs", str(inputs.send_backs)]
    if inputs.coordinator:
        argv += ["--coordinator-mode", inputs.coordinator]
    if inputs.escalated is not None:
        argv += ["--escalated", inputs.escalated]
    result = subprocess.run(argv + ["--playbook", inputs.station, "--attempt", inputs.attempt], capture_output=True, text=True)
    if result.returncode and "unknown playbook" in result.stderr:
        result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode:
        raise BrigadeError(f"roles.py mode refused the brief: {result.stderr.strip().removeprefix('error: ')}")
    lines = tuple(result.stdout.splitlines())
    modes = [line.removeprefix("Mode: ") for line in lines if line.startswith("Mode: ")]
    if len(modes) != 1 or modes[0] not in MODES:
        raise BrigadeError("roles.py mode printed no Mode: full or Mode: light line")
    source = next((line.removeprefix("Mode source: ") for line in lines if line.startswith("Mode source: ")), "")
    return ModeLines(lines, modes[0], source.removeprefix(ESCALATED) if source.startswith(ESCALATED) else None)


def brief(restaurant, ident, goal, acceptance, contract, verify, paths, lease, base, context):
    for _ in range(MODE_RESOLUTIONS):
        with restaurant.checked():
            restaurant.fence()
            dish, used_paths, _, thread = brief_dish(restaurant, ident, paths, lease, acceptance)
            inputs = mode_inputs(restaurant, dish, used_paths, thread)
        mode = seam.roles_mode(inputs)
        with restaurant.checked():
            restaurant.fence()
            dish, used_paths, used_lease, thread = brief_dish(restaurant, ident, paths, lease, acceptance)
            if mode_inputs(restaurant, dish, used_paths, thread) != inputs:
                continue
            if inputs.escalated is None and mode.escalation:
                record_escalation(restaurant, ident, mode.escalation)
            return write_brief(restaurant, dish, mode, goal, acceptance, contract, verify, used_paths, used_lease, base,
                               context, thread)
    raise BrigadeError(f"{ident} changed while resolving its mode; run brief again")


def verify_rule(land):
    return (
        "While working, run only the affected test modules. "
        "Run the claims check before the final verification. "
        f"Run the full verification chain once, after the last commit, through `python3 {land} slot -- <command>`. "
        "The chain is the first bullet of this section. "
        "A later commit that changes only prose does not rerun it. "
        "A fix round runs the affected modules while working and one full run at the end."
    )


def write_brief(restaurant, dish, mode, goal, acceptance, contract, verify, paths, lease, base, context, thread):
    ident = dish["id"]
    if not dish["branch"]:
        dish = restaurant.update("dishes.tsv", ident, "dish", branch=f"{slug(restaurant.meta['restaurant'])}/{ident.lower()}")
    tickets = {row["id"]: row for row in restaurant.rows("rail.tsv")}
    land = seam.LAND
    report = restaurant.dir / "reports" / f"{ident}.md"
    sent_back = [row for row in item_verdicts(restaurant.rows("pass.tsv"), ident) if VERDICTS.get(row["verdict"]) == "sent-back"]
    findings = restaurant.dir / "reports" / (sent_back[-1]["report"] if sent_back and sent_back[-1]["report"] else f"{ident}-review.md")
    lines = [
        f"Use the poteto-mode skill and its `{dish['station']}` playbook.", *mode.lines, "Gate: brigade", SEAT_RULE, "",
        f"GOAL: {goal}",
        f"PURPOSE: {menu_purpose(restaurant)}",
        f"TICKETS: " + "; ".join(f"{t}: {tickets[t]['summary']}" for t in dish["tickets"].split(",") if t in tickets), "",
        "SCOPE:",
        f"- You run in your own git worktree on branch `{dish['branch']}`, started from `{base}`. Work only there, and commit on that branch.",
        f"- Change only these paths, leased to you as {lease}: {paths}. A change outside them is refused when it lands. Report it instead of making it.",
        "- Keep history linear: no merge commits. Never merge, rebase a shared branch, push trunk, or open a PR. The landing queue does that.",
        "", "CONTEXT:", *([f"- {item}" for item in context] or ["- None beyond the tickets."]),
        *([f"- A reviewer sent an earlier attempt back. Its findings: {findings}"] if findings.exists() else []),
        "", "ACCEPTANCE:", *[f"- {item}" for item in acceptance],
        "", "CONTRACT:", contract,
        "", "VERIFY:", f"- {verify}",
        f"- {verify_rule(land)}",
        *([f"- Run every measurement through `python3 {land} slot --exclusive -- <command>`, so no other work shares the machine while it runs."]
          if dish["station"] in MEASURING_STATIONS else []),
        "", f"TIMEBOX: {dish.get('timebox') or 60} minutes. The timebox orders the work and never waives a playbook step (How, Architect, investigation, or the implementation delegate). At the limit, write the report with what remains instead of skipping steps.",
        "", "REPORT:",
        WAIT_RULE,
        f"- Write it to {report}: status, branch, head SHA, what you ran and its output, before and after numbers with the method, deviations, follow-ups. "
        "List each follow-up as one top-level list item under a `## Follow-ups` heading, or write `None.` there. "
        "Name each file a follow-up touches by its repository path in backticks.",
        "- Under the status line, repeat this brief's Mode: line, and its Waived by mode: line when it has one. A step that line names is not a deviation.",
        *(["- If you find the design contested, do not run interrogate. Stop at a verifiable point, commit, and write Contested: <one-line reason> under the status line. The coordinator moves the work to full mode and gives your report to a fresh worker."]
          if mode.mode == "light" else []),
        f"- After that file is written, call t3_thread_send to thread {thread} with mode \"auto\" and the one-line message \"{ident} done: report at {report}\".",
        "- Then end your turn with one line naming the report path.",
        "", "STANDING ORDERS:", (restaurant.dir / "house-rules.md").read_text().strip() + "\n\n" + "\n".join(TEST_STANDARD),
    ]
    text = "\n".join(lines) + "\n"
    restaurant.write(contract_file(ident), contract + "\n")
    restaurant.write(Path("briefs") / f"{ident}.md", text)
    return text


def contract_file(ident):
    return Path("briefs") / f"{ident}.contract.md"


def stored_contract(restaurant, ident):
    """The contract the item's last brief stored.

    It is the contract file's text, with no newline converted and without the one newline write_brief put after the contract.
    """
    restaurant.find("dishes.tsv", ident)
    path = restaurant.dir / contract_file(ident)
    if not path.is_file():
        raise BrigadeError(f"{ident} has no contract on record; run brief with --contract")
    return path.read_bytes().decode("utf-8").removesuffix("\n")
