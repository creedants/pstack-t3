import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from brigade_lib import seam
from brigade_lib._store import rules
from brigade_lib.affected import layout as affected_layout, verify_text as affected_verify_text
from brigade_lib.items import attempt_kind, latest_mode_note, menu_purpose, record_escalation
from brigade_lib.leases import kind_limits
from brigade_lib.reviews import fix_round_refusal, item_verdicts, send_backs
from brigade_lib.shared import BrigadeError, same_text, slug
from brigade_lib.store import HELD_LOCKS, MODES, SCRATCH_KEY, SCRATCH_REFUSAL, VERDICTS, roles_script, scratch_parent_text



MEASURING_STATIONS = ("perf-issue", "hillclimb", "eval")


@dataclass(frozen=True)
class _Standing:
    """One rule the brief pastes verbatim, with the anchors that show the coordinator already stated it.

    The placeholders in `text` and the anchors are `<reports>` and `<scratch>`. A rule whose
    placeholder has no value is not rendered. A rule is dropped when the coordinator's context and verify
    text already hold every one of its anchors.
    """
    text: str
    anchors: tuple


# The standing rules, in the order the brief prints them.
_RULES = (
    _Standing(
        "Save the full output of any failed test run to a file under <reports>/ before you rerun it, "
        "and name that file in the report.",
        ("<reports>/", "failed test run", "before you rerun", "Save the full output"),
    ),
    _Standing(
        "Make every scratch directory, archive copy, and saved output with `mktemp -d -p <scratch>`, never "
        "directly under `/tmp`. Delete that directory when you are done. A test suite's own temporary files stay "
        "where the tests put them. Do not set TMPDIR.",
        ("mktemp -d -p <scratch>",),
    ),
)
STANDING_PLACEHOLDER = re.compile(r"<([a-z]+)>")
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
    """The inputs to the brief's mode resolution. brief is the review depth recorded for the dish's kind, or "" when it has none.

    The other fields are the project root, the station, the attempt, the leased paths, the send-back count, the
    restaurant's recorded mode, the item's recorded escalation, the dish row, the coordinator thread, and the
    store's generation.
    """
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
    brief: str = ""


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
    rules.house_rules(restaurant.dir / "house-rules.md")
    lines = house_rules_cap_lines(restaurant)
    if lines:
        raise BrigadeError("\n".join(lines))
    return dish, paths, lease, thread


def mode_inputs(restaurant, dish, paths, thread):
    meta = restaurant.meta
    events = restaurant.rows("log.tsv")
    sent_back = len(send_backs(restaurant.rows("pass.tsv"), dish["id"]))
    limits = kind_limits(meta)
    return ModeInputs(meta["projectRoot"], dish["station"], attempt_kind(events, dish["id"]), paths, sent_back,
                      meta.get("mode") if meta.get("mode") in MODES else None, latest_mode_note(events, dish["id"]),
                      dish, thread, meta.get("generation"), limits.review.get(dish["kind"]) or "")


def roles_mode(inputs):
    if HELD_LOCKS:
        raise BrigadeError(f"roles.py may run git; release {HELD_LOCKS[0]}'s store lock before resolving the mode")
    argv = [sys.executable, str(roles_script()), "mode", "--cwd", inputs.project_root, "--paths", inputs.paths,
            "--send-backs", str(inputs.send_backs)]
    if inputs.brief:
        argv += ["--brief-mode", inputs.brief]
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
            meta = restaurant.meta
        mode = seam.roles_mode(inputs)
        picked_layout = affected_layout(meta)
        if not (verify or "").strip():
            picked = affected_verify_text(inputs.project_root, used_paths, seam.LAND, meta)
        else:
            picked = verify
        with restaurant.checked():
            restaurant.fence()
            dish, used_paths, used_lease, thread = brief_dish(restaurant, ident, paths, lease, acceptance)
            if mode_inputs(restaurant, dish, used_paths, thread) != inputs:
                continue
            if inputs.escalated is None and mode.escalation:
                record_escalation(restaurant, ident, mode.escalation)
            return write_brief(restaurant, dish, mode, goal, acceptance, contract, picked, used_paths, used_lease, base,
                               context, thread, picked_layout)
    raise BrigadeError(f"{ident} changed while resolving its mode; run brief again")


def verify_rule(land, picked_layout):
    lines = []
    if picked_layout is not None:
        lines.append("While working, run only the affected test modules. ")
    lines.append(f"Run all builds and tests through `python3 {land} slot -- <command>`. ")
    if picked_layout is not None:
        lines.append(f"When the brief asks for the whole suite, run it as `python3 {land} slot --slots 2 -- {picked_layout.suite}`. ")
        lines.append("When that command is the outermost slot, a governor with fewer than two worker slots refuses it and the suite does not run. ")
        lines.append("An affected-module run stays one slot. ")
    lines.extend([
        "When this section has an exclusive slot rule for measurements, that rule takes precedence for measurements. ",
        "Run the claims check before the final verification. ",
        "Run the full verification chain once, after the last commit. ",
        "The chain is the first bullet of this section. ",
        "A later commit that changes only prose does not rerun it. ",
        "Run the claims check again after any later edit to prose. ",
        "Prose is a change note, a comment, a docstring, help text, or a report sentence the check covered. ",
    ])
    if picked_layout is not None:
        lines.append(f"The final run of the chain also includes `python3 -m unittest {picked_layout.always}`. ")
        lines.append("A prose-only commit runs that module and does not rerun the chain. ")
        lines.append("A fix round runs the affected modules while working and one full run at the end.")
    return "".join(lines)


def _fill_standing(text, values):
    return STANDING_PLACEHOLDER.sub(lambda match: values[match.group(1)], text)


def standing_rules(restaurant, context, verify):
    """The STANDING RULES bullets, in _RULES order.

    A rule whose placeholder has no value is skipped. A rule is dropped when the coordinator's context and
    verify text already hold every one of its anchors.
    """
    try:
        scratch = scratch_parent_text(restaurant.meta.get(SCRATCH_KEY))
    except BrigadeError:
        raw = restaurant.meta.get(SCRATCH_KEY)
        raise BrigadeError(f"restaurant.json {SCRATCH_KEY} {raw!r} is invalid: {SCRATCH_REFUSAL}") from None
    values = {"reports": str(restaurant.dir / "reports"), "scratch": scratch}
    seen = same_text(" ".join(context) + " " + verify)
    lines = []
    for rule in _RULES:
        if any(not values.get(name) for name in STANDING_PLACEHOLDER.findall(rule.text)):
            continue
        text = _fill_standing(rule.text, values)
        anchors = tuple(_fill_standing(anchor, values) for anchor in rule.anchors)
        for anchor in anchors:
            if anchor not in text:
                raise BrigadeError(f"standing rule anchor {anchor!r} is not in {text!r}")
        scoped = "<scratch>" in rule.text
        seen_all = True
        for anchor in anchors:
            needle = same_text(anchor)
            if scoped:
                if re.search(re.escape(needle) + r"(?![A-Za-z0-9_.\-~])", seen) is None:
                    seen_all = False
                    break
            elif needle not in seen:
                seen_all = False
                break
        if seen_all:
            continue
        lines.append(f"- {text}")
    return lines


def worker_house_rules(text):
    """The house rules a worker brief carries. A rule tagged for another role is left out, and a worker tag and at most one space after it are removed."""
    return rules.for_role("worker", text)


def house_rules_cap_lines(restaurant):
    """One line for each role whose house rules exceed the store's houseRulesCap."""
    cap = rules.cap_of(restaurant.meta)
    if cap is None:
        return ()
    path = restaurant.dir / "house-rules.md"
    return rules.breach_lines(cap, path.read_text() if path.is_file() else "")


def write_brief(restaurant, dish, mode, goal, acceptance, contract, verify, paths, lease, base, context, thread,
                picked_layout):
    ident = dish["id"]
    standing_orders = rules.house_rules(restaurant.dir / "house-rules.md")
    if not dish["branch"]:
        dish = restaurant.update("dishes.tsv", ident, "dish", branch=f"{slug(restaurant.meta['restaurant'])}/{ident.lower()}")
    tickets = {row["id"]: row for row in restaurant.rows("rail.tsv")}
    land = seam.LAND
    report = restaurant.dir / "reports" / f"{ident}.md"
    sent_back = [row for row in item_verdicts(restaurant.rows("pass.tsv"), ident) if VERDICTS.get(row["verdict"]) == "sent-back"]
    findings = restaurant.dir / "reports" / (sent_back[-1]["report"] if sent_back and sent_back[-1]["report"] else f"{ident}-review.md")
    standing = standing_rules(restaurant, context, verify)
    lines = [
        f"Use the poteto-mode skill and its `{dish['station']}` playbook.", *mode.lines, "Gate: brigade", SEAT_RULE, "",
        f"GOAL: {goal}",
        f"PURPOSE: {menu_purpose(restaurant)}",
        "TICKETS: " + "; ".join(f"{t}: {tickets[t]['summary']}" for t in dish["tickets"].split(",") if t in tickets), "",
        "SCOPE:",
        f"- You run in your own git worktree on branch `{dish['branch']}`, started from `{base}`. Work only there, and commit on that branch.",
        f"- Change only these paths, leased to you as {lease}: {paths}. A change outside them is refused when it lands. Report it instead of making it.",
        "- Keep history linear: no merge commits. Never merge, rebase a shared branch, push trunk, or open a PR. The landing queue does that.",
        "", "CONTEXT:", *([f"- {item}" for item in context] or ["- None beyond the tickets."]),
        *([f"- A reviewer sent an earlier attempt back. Its findings: {findings}"] if findings.exists() else []),
        "", "ACCEPTANCE:", *[f"- {item}" for item in acceptance],
        "", "CONTRACT:", contract,
        "", "VERIFY:", f"- {verify}",
        f"- {verify_rule(land, picked_layout)}",
        *([f"- Run every measurement through `python3 {land} slot --exclusive -- <command>`, so no other work shares the machine while it runs."]
          if dish["station"] in MEASURING_STATIONS else []),
        *(["", "STANDING RULES:", *standing] if standing else []),
        "", f"TIMEBOX: {dish.get('timebox') or 60} minutes. The timebox orders the work and never waives a playbook step (How, Architect, investigation, or the implementation delegate). At the limit, write the report with what remains instead of skipping steps.",
        "", "REPORT:",
        WAIT_RULE,
        f"- Write it to {report}: status, branch, head SHA, what you ran and its output, before and after numbers with the method, deviations, follow-ups. "
        "List each follow-up as one top-level list item under a `## Follow-ups` heading, or write `None.` there. "
        "Mark a follow-up that is safety, privacy, or correctness with `Safety:`, `Privacy:`, or `Correctness:` at the start of its text, after any list marker. "
        "Only a marked follow-up can become a ticket, at most one per item. "
        "Every other follow-up goes to the store's notes ledger, unless its text is already on the rail. "
        "Name each file a follow-up touches by its repository path in backticks.",
        "- Hold a `## Threads` section with one list item per child of the form - <role> `<thread id>` <seat>. The role is one of code delegate, claims check, and architect runner, named literally. The seat is one bare provider/model token with nothing after it and no backticks around it. That means no reasoning level, no note, and no effort word after the seat. List only code delegate, claims check, and architect runner under ## Threads. Put every other child under ## Notes. List only T3 threads started with `delegate_task` or `t3_thread_launch` for those threads. For example, the line - code delegate `mcp:00000000-0000-4000-8000-000000000001` opencode-go/step-5-preview-free parses as one child with no unreadable line. Name a child run inside the worker's own harness in the report body instead, never in the section. A missing section adds no row and is not an error. The worker still writes the section when it used a child.",
        "- Under the status line, repeat this brief's Mode: line, and its Waived by mode: line when it has one. A step that line names is not a deviation.",
        *(["- If you find the design contested, do not run interrogate. Stop at a verifiable point, commit, and write Contested: <one-line reason> under the status line. The coordinator moves the work to full mode and gives your report to a fresh worker."]
          if mode.mode == "light" else []),
        f"- After that file is written, call t3_thread_send to thread {thread} with mode \"auto\" and the one-line message \"{ident} done: report at {report}\".",
        "- If `t3_thread_send` returns thread_not_found, read the thread id again from this brief file, copy it exactly, and retry once; name both attempts in the final message.",
        "- Then end your turn with one line naming the report path.",
        "", "STANDING ORDERS:", worker_house_rules(standing_orders),
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
