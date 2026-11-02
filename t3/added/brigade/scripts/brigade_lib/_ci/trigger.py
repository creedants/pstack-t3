from dataclasses import dataclass
from datetime import datetime, timezone

CI_SOURCE = "ci-time"
CI_REF = "ci-time"
CI_PATH = ".github/workflows"
CI_DECISION_KIND = "ci-time"
TRY_AGAIN = "try again"
LEAVE_IT = "leave it"
CI_OPTIONS = f"{TRY_AGAIN}, {LEAVE_IT}"

ARMED = "armed"
TICKET_LIVE = "ticket-live"
COOLING = "cooling"
ASKING = "asking"
RETRYING = "retrying"
STOPPED = "stopped"


@dataclass(frozen=True)
class CiRun:
    """One merged pull request's CI record. merged_at is timezone aware."""

    pr: int
    merged_at: datetime
    ci_seconds: int
    open_to_merge_seconds: int
    clean: bool
    lost: int
    time_limit: int


@dataclass(frozen=True)
class Breach:
    """Two most recent clean runs that are both over the target, oldest first."""

    older: CiRun
    newer: CiRun


def _aware(moment):
    """The moment read as UTC when it is naive."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _at(text):
    """The ISO stamp parsed, read as UTC when it is naive."""
    return _aware(datetime.fromisoformat(text.replace("Z", "+00:00")))


def _key(text):
    """The option text compared case and trailing-dot blind, like closing_options."""
    return text.strip().rstrip(".").casefold()


def _ci_tickets(tickets):
    """The rail rows this trigger filed, whatever state they reached."""
    return [row for row in tickets if (row.get("source") or "").split(" (", 1)[0] == CI_SOURCE]


def _ci_decisions(decisions):
    """The 86 rows this trigger added."""
    return [row for row in decisions if (row.get("kind") or "") == CI_DECISION_KIND]


def _latest(texts):
    """The latest ISO stamp of these, or empty when there is none."""
    best = ""
    for text in texts:
        if text and (not best or _at(text) > _at(best)):
            best = text
    return best


def phase_of(tickets, decisions, finished_at, target_at, live):
    """The trigger's phase from its derived state. No stored phase is read or written.

    A live ci-time ticket blocks everything. An open ci-time decision holds the loop
    while other work continues. A leave-it answer stops the trigger until the target
    is set again after it. A try-again answer rearms filing. A finished trigger with
    no decision after it is cooling toward a decision. Anything else is armed.
    """
    own = {row["id"] for row in _ci_tickets(tickets)}
    if any(ident in live for ident in own):
        return TICKET_LIVE
    mine = _ci_decisions(decisions)
    if any(row["state"] == "open" for row in mine):
        return ASKING
    last_finish = _latest(finished_at.get(ident, "") for ident in own)
    answered = [row for row in mine if row["state"] == "answered"]
    answer_at = ""
    if answered:
        last = max(answered, key=lambda row: _at(row.get("answered") or "1970-01-01T00:00:00+00:00"))
        answer = _key(last.get("answer") or "")
        answer_at = last.get("answered") or ""
        if target_at and _at(target_at) > _at(answer_at) and (not last_finish or _at(target_at) > _at(last_finish)):
            return ARMED
        if not last_finish or _at(answer_at) >= _at(last_finish):
            if answer == _key(LEAVE_IT):
                return STOPPED
            if answer == _key(TRY_AGAIN):
                return RETRYING
    if last_finish:
        return COOLING
    return ARMED


def breach(runs, target_minutes, since):
    """The breach of the target among these runs, or None.

    Only runs merged after the last finished trigger ticket count. The two most
    recent clean runs both strictly over the target are a breach.
    """
    ordered = sorted(runs, key=lambda run: (_aware(run.merged_at), run.pr))
    if since:
        start = _at(since)
        ordered = [run for run in ordered if _aware(run.merged_at) > start]
    limit = target_minutes * 60
    clean_runs = [run for run in ordered if run.clean]
    if len(clean_runs) < 2:
        return None
    older, newer = clean_runs[-2], clean_runs[-1]
    if older.ci_seconds > limit and newer.ci_seconds > limit:
        return Breach(older, newer)
    return None
