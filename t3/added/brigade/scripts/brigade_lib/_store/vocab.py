TICKET_STATES = ("waiting", "assigned", "moved", "done", "dropped")


LIVE_TICKET_STATES = ("waiting", "assigned")


DISH_STATES = ("in-progress", "in-review", "passed", "sent-back", "blocked", "queued", "merged", "dropped")


COUNTED_STATES = ("in-progress", "in-review")


LEASED_STATES = ("in-progress", "in-review", "passed", "sent-back", "blocked")


IN_FLIGHT_STATES = (*LEASED_STATES, "queued")


OPEN_LINES = {"in-review": "in review", "passed": "passed, not submitted", "sent-back": "sent back", "blocked": "parked"}


VERDICTS = {"pass": "passed", "send-back": "sent-back", "blocked": "blocked", "pass-with-edits": "sent-back"}


REASONS = ("false-wording", "missing-test", "wrong-behavior", "out-of-lease", "scope", "other")


REASON_VERDICTS = ("send-back",)


UNRECORDED_REASON = "unrecorded"


THREAD_ROLES = ("worker", "fix worker", "reviewer", "panel member", "code delegate", "claims check", "architect runner")


WORKER_ROLES = ("worker", "fix worker")


CHILD_ROLES = ("code delegate", "claims check", "architect runner")


THREAD_COMMAND_ROLES = ("panel member",)


THREADS_WRITERS = {"worker": "dish <item> --thread <id>", "fix worker": "dish <item> --thread <id>",
                   "reviewer": "pass record --reviewer-thread <id>", "panel member": "thread <dish> --role \"panel member\""}


EDITS_VERDICT = "pass-with-edits"


MODES = ("full", "light")


ROUND_BUDGET = 3


COOLDOWN_MINUTES = 60


OWING_STATES = ("in-progress", "in-review", "sent-back", "blocked")


ROUND_DECISIONS = ("known limits", "redesign", "drop")


ROUND_PARKED = "keep parked"


ROUND_KIND = "round-budget"


OPEN_RUN_MINUTES = 10


PRIORITIES_BY_RANK = ("urgent", "normal", "low")


AUTOFIRE = ("off", *PRIORITIES_BY_RANK)


SOURCE_PRIORITY = {"upstream": "urgent", "report": "low"}


KINDS = ("feature", "fix", "polish", "upkeep", "study")


KIND_CHOICE = f"<{'|'.join(KINDS)}>"


STATION_KIND = {"bug-fix": "fix", "perf-issue": "fix", "investigation": "study"}


GOAL_STATES = ("open", "paused", "done", "dropped")


CLOSED_GOAL_STATES = ("done", "dropped")


GOAL_MOVES = {"open": ("paused", "done", "dropped"), "paused": ("open", "done", "dropped"),
              "done": ("open",), "dropped": ("open",)}


LOW_RESERVE = 1


LOW_FLAG_DAYS = 7


GENERATED_ROOT = "skills/"


SOURCE_ROOT = "t3/"


LINE_SUFFIX = r":\d+(?:-\d+)?$"


TABLES = {
    "rail.tsv": ("id", "at", "state", "source", "ref", "dish", "summary", "priority", "paths", "decision", "kind", "goal", "after"),
    "dishes.tsv": ("id", "at", "state", "station", "tickets", "task", "thread", "branch", "pr", "sha", "summary", "timebox", "lease", "paths", "reported",
                   "kind", "goal"),
    "pass.tsv": ("at", "dish", "pr", "sha", "verdict", "author", "verifier", "note", "report", "member", "reason"),
    "threads.tsv": ("at", "dish", "attempt", "role", "thread", "seat", "start", "end"),
    "86.tsv": ("id", "at", "state", "dish", "question", "options", "default", "answer", "kind", "answered"),
    "log.tsv": ("at", "kind", "id", "state", "note"),
    "edits.tsv": ("at", "dish", "from", "to", "report"),
    "goals.tsv": ("id", "at", "state", "name", "purpose", "done", "share", "closed"),
    # Only the executive admin's store has this table.
    "rulings.tsv": ("id", "at", "kind", "parties", "question", "rule", "decision", "supersedes", "state"),
}


ADDED_COLUMNS = {"pass.tsv": 3, "86.tsv": 2, "rail.tsv": 6, "dishes.tsv": 2}


PREFIX = {"rail.tsv": "T", "dishes.tsv": "D", "86.tsv": "Q", "rulings.tsv": "R", "goals.tsv": "G"}


ADMIN_DIR = ".admin"


ADMIN_NAME = "executive admin"


WORK_COMMANDS = ("fire", "brief", "contract", "dish", "pass", "edits", "watch", "startable", "next", "thread", "hang")


ADMIN_LINES = (
    ("merged", "dish merged", False),
    ("sent-back", "dish sent-back or blocked", False),
    ("decision", "decision open", False),
    ("failed", "none", True),
    ("drained", "none", True),
    ("report", "none", True),
    ("blocked", "ticket blocked", False),
    ("misrouted", "ticket moved", False),
    ("reply", "none", True),
    ("contest", "none", True),
    ("appeal", "none", True),
)


UNSENT = tuple(kind for kind, _, _ in ADMIN_LINES)


MESSAGE_ONLY = frozenset(kind for kind, _, only in ADMIN_LINES if only)


ADMIN_COMMANDS = ("request", "rule", "sync")


RULING_KINDS = ("contested-paths", "ownership", "shares", "queue-order")


RULING_RULES = ("purpose", "priority", "age", "related-work", "dependency", "floor", "user")


RULING_STATES = ("in-force", "done", "expired", "superseded", "overruled")


NOTHING_HANDED = "nothing handed to you"


NEEDS_CONTRACT = ("brief needs a contract; pass --contract <text> or the contract key in --fields, and write none when the item has no safety, privacy, or integrity property")


REPORTING = ("every-turn", "milestones", "digest")


CLAIM_WAIT_SECONDS = 1.0


SNAPSHOT_CHUNK = 1 << 16
