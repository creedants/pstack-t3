import re
from datetime import datetime
from pathlib import Path
from brigade_lib import seam
from brigade_lib._affected.report import contract_check, records_check
from brigade_lib._reviews.rounds import item_verdicts, roles_family, round_debt
from brigade_lib._reviews.threads import report_threads, report_threads_refusal, write_threads
from brigade_lib.edits import NEEDS_COMMANDS, commit_tree, replay, report_edits
from brigade_lib.reports import NOT_A_REPORT, REVIEW_FILE, in_reports, report_stat
from brigade_lib.shared import BrigadeError, clean
from brigade_lib.store import EDITS_VERDICT, REASON_VERDICTS, VERDICTS

CLAIMS_LINE = re.compile(r"^[ \t#>*+\-_`\d.)]*Claims check", re.M)


CLAIMS_STATES = ("in-review", "passed")


def review_report(restaurant, dish_id, report):
    """The bare name of `report`.

    A name that is not a review report of the item is refused. So is a path that in_reports turns down, and so is a
    name for which report_entry does not call reports/<name> a `file`.
    """
    name = Path(report).name
    match = REVIEW_FILE.fullmatch(name)
    if not match or match.group(1) != dish_id:
        raise BrigadeError(f"{name} is not a review report of {dish_id}; name a file like reports/{dish_id}-review-1.md")
    if not in_reports(restaurant, report, name):
        raise BrigadeError(f"{report} is outside this store's reports/; name reports/{name}")
    kind = seam.report_entry(restaurant, name)
    if kind == "missing":
        raise BrigadeError(f"reports/{name} does not exist; write the review report first")
    if kind != "file":
        raise BrigadeError(f"reports/{name} {NOT_A_REPORT[kind]}; nothing recorded")
    return name


def worker_report(restaurant, dish_id):
    """The text of reports/<dish>.md, or None when the item has no report.

    A report that is a symbolic link, is not a regular file, or resolves outside this store's reports/ is
    refused with the words the store already uses for that kind. So is one that is not UTF-8.
    """
    name = f"{dish_id}.md"
    kind = seam.report_entry(restaurant, name)
    if kind == "missing":
        return None
    if kind != "file":
        raise BrigadeError(f"reports/{name} {NOT_A_REPORT[kind]}; nothing recorded")
    try:
        return (restaurant.dir / "reports" / name).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise BrigadeError(f"reports/{name} is not UTF-8; nothing recorded") from None


def record_pass(restaurant, dish_id, pr, sha, verdict, author, verifier, note="", same_family=False, report="",
                member=False, late=False, edits="", reviewer_thread="", reason=""):
    _, dish = restaurant.find("dishes.tsv", dish_id)
    if verdict not in VERDICTS:
        raise BrigadeError(f"verdict must be one of {', '.join(VERDICTS)}")
    if reason and verdict not in REASON_VERDICTS:
        raise BrigadeError(f"--reason goes only with --verdict {', '.join(REASON_VERDICTS)}")
    if reviewer_thread and member:
        raise BrigadeError("--member takes no --reviewer-thread; record a panel member's row with "
                           f"thread {dish_id} --role \"panel member\" --thread <id> --seat <provider/model>")
    if roles_family()(author) == roles_family()(verifier) and not same_family:
        raise BrigadeError(f"verifier {verifier} is the same model family as author {author}; "
                           "pick a verifier from another family, or pass --same-family when no other family is runnable")
    if same_family:
        note = clean(f"same model family; {note}")
    if verdict == EDITS_VERDICT:
        if not edits:
            raise BrigadeError(f"--verdict {EDITS_VERDICT} needs --edits <review report>")
        if report:
            raise BrigadeError("--edits names the round's report; drop --report")
        report = edits
    elif edits:
        raise BrigadeError(f"--edits goes only with --verdict {EDITS_VERDICT}")
    if report:
        report = review_report(restaurant, dish_id, report)
    if verdict == EDITS_VERDICT:
        report_edits(restaurant, report)
    if verdict == "pass" and not member:
        edits_gate(restaurant, dish_id, sha, report)
    # Read the report before the append, so a line the parser refuses leaves no pass row and no thread row.
    body = worker_report(restaurant, dish_id)
    children, unreadable = report_threads(body) if body else ((), ())
    if unreadable:
        raise BrigadeError(report_threads_refusal(restaurant, dish_id, unreadable))
    restaurant.append("pass.tsv", {"at": seam.now(), "dish": dish_id, "pr": pr, "sha": sha, "verdict": verdict,
                                   "author": author, "verifier": verifier, "note": note, "report": report,
                                   "member": "yes" if member else "", "reason": reason})
    rows = [("reviewer", reviewer_thread, verifier)] if reviewer_thread else []
    rows += [(child["role"], child["thread"], child["seat"]) for child in children]
    write_threads(restaurant, dish_id, rows)
    if member:
        return f"{dish_id}: member {verdict} on record; {dish_id} stays {dish['state']}"
    if late:
        printed = f"{dish_id}: late {verdict} on record; {dish_id} stays {dish['state']}"
    else:
        dish = restaurant.update("dishes.tsv", dish_id, "dish", state=VERDICTS[verdict], pr=pr, sha=sha)
        printed = f"{dish_id} {dish['state']}"
    debt = round_debt(restaurant, dish) if verdict == "send-back" else 0
    if debt:
        return f"{printed}; {debt} send-backs, decision pending"
    return printed


def edits_gate(restaurant, dish_id, sha, report):
    """Refuse a pass while the item's last verdict row is pass-with-edits.

    Two things lift it. One is an edits.tsv row for that round's sha, this sha, and that round's report.
    The other is a `report` that is not that round's report.
    """
    verdicts = item_verdicts(restaurant.rows("pass.tsv"), dish_id)
    if not verdicts or verdicts[-1]["verdict"] != EDITS_VERDICT:
        return
    last = verdicts[-1]
    if any(row["dish"] == dish_id and (row["from"], row["to"], row["report"]) == (last["sha"], clean(sha), last["report"])
           for row in restaurant.rows("edits.tsv")):
        return
    if report and report != last["report"]:
        return
    raise BrigadeError(f"{dish_id}'s last verdict is {EDITS_VERDICT} at {last['sha']}; run edits verify {dish_id} "
                       f"--from {last['sha']} --to {sha}, or name a second review's report with --report")


def verify_edits(restaurant, dish_id, from_sha, to_sha):
    """Called from handle(), because it starts subprocesses. It reads projectRoot and the landing contract before taking the store lock. It holds the lock while checking tables and appending edits.tsv, and runs subprocesses outside the lock."""
    contract = contract_check(restaurant.meta.get("projectRoot") or "")
    with restaurant.checked():
        restaurant.fence()
        restaurant.find("dishes.tsv", dish_id)
        rounds = [row for row in item_verdicts(restaurant.rows("pass.tsv"), dish_id)
                  if row["verdict"] == EDITS_VERDICT and row["sha"] == from_sha]
        if not rounds:
            raise BrigadeError(f"{dish_id} has no {EDITS_VERDICT} round at {from_sha}")
        meta = restaurant.meta
        build, check = meta.get("buildCommand"), meta.get("checkCommand")
        if not records_check(meta):
            check = contract
        if not build or not check:
            raise BrigadeError(NEEDS_COMMANDS)
    name = rounds[-1]["report"]
    kind = seam.report_entry(restaurant, name)
    if kind != "file":
        raise BrigadeError(f"reports/{name} {NOT_A_REPORT[kind]}; nothing verified")
    edits = report_edits(restaurant, name)
    (reviewed, before), (_, after) = (commit_tree(meta["projectRoot"], sha) for sha in (from_sha, to_sha))
    lines, verified = replay(meta["projectRoot"], reviewed, before, after, edits, build, check, from_sha)
    for line in lines:
        print(line)
    if not verified:
        raise BrigadeError(f"{dish_id}: edits not verified, {from_sha} to {to_sha}")
    row = {"dish": dish_id, "from": clean(from_sha), "to": clean(to_sha), "report": name}
    with restaurant.checked():
        restaurant.fence()
        if not any(all(recorded[key] == value for key, value in row.items()) for recorded in restaurant.rows("edits.tsv")):
            restaurant.append("edits.tsv", {"at": seam.now(), **row})
    return f"{dish_id}: edits verified, {from_sha} to {to_sha}"


def unrecorded_reviews(restaurant):
    """(name, kind) for each entry under reports/ named like a review report that no pass.tsv row accounts for.

    The kind is report_stat's. A row of the entry's dish that names it in `report` accounts for it, and nothing is read
    from that entry. A row that names no report cannot say which file it reviewed, so it accounts for every `file` of its
    dish written at or before it, and for no entry of another kind. The time compared is the one in report_stat's status.
    An entry that report_stat calls `missing` is left out.
    """
    rows = restaurant.rows("pass.tsv")
    reports = restaurant.dir / "reports"
    unrecorded = []
    for path in sorted(reports.iterdir()) if reports.is_dir() else []:
        match = REVIEW_FILE.fullmatch(path.name)
        if not match:
            continue
        mine = [row for row in rows if row["dish"] == match.group(1)]
        if any(row["report"] == path.name for row in mine):
            continue
        kind, status = report_stat(restaurant, path.name)
        if kind == "file" and any(datetime.fromisoformat(row["at"].replace("Z", "+00:00")).timestamp() >= status.st_mtime
                                  for row in mine if not row["report"]):
            continue
        if kind != "missing":
            unrecorded.append((path.name, kind))
    return unrecorded
