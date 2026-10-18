import re
from brigade_lib import seam
from brigade_lib.edits import HEADING
from brigade_lib.shared import BrigadeError, clean, fence_close, one_of, stored, threads_fence_open
from brigade_lib.store import CHILD_ROLES, THREADS_WRITERS, WORKER_ROLES

THREADS_SECTION = (2, "Threads")


THREADS_ITEM = re.compile(r"^- ([a-z]+(?: [a-z]+)*) `([^`]+)` (\S+)$")


THREADS_ITEM_START = re.compile(r"[-*+\u2022]|\d+[.)]")


THREADS_ITEM_THREAD = re.compile(r"`[^`\s]*:[^`\s]*`")


THREADS_REFUSED = {
    "item": "is not one list item of the form - <role> `<thread id>` <seat>",
    "id": "holds a thread id that the table would change",
    "deeper": "is a heading deeper than the ## Threads heading",
    "again": "is a second ## Threads heading",
    "role": "holds role {role!r}, which is not {roles}",
    "writer": "holds role {role!r}, which {writer} writes",
}


THREADS_REFUSED_LINE = "{dish}: reports/{name} cannot be read; {lines}; nothing recorded"


def threads_prose(line):
    """Whether a non-blank line of the ## Threads section is plain prose, so report_threads skips it.

    A prose line starts, after any leading whitespace, with neither a list marker nor a `#`, holds no
    backticked thread id, that is no backticked value holding a colon and holding no whitespace, and
    names no child role, that is no lowercase code delegate, claims check, or architect runner. A list marker is a
    hyphen, an asterisk, a plus, a bullet, or a number followed by a dot or a closing parenthesis.
    """
    stripped = line.lstrip()
    if THREADS_ITEM_START.match(stripped) or stripped.startswith("#"):
        return False
    if THREADS_ITEM_THREAD.search(line):
        return False
    return not any(role in line for role in CHILD_ROLES)


def report_threads(text):
    """The children a report's ## Threads section lists, and every line that could not be read.

    The children are a tuple of dicts holding `role`, `thread`, and `seat`. The unreadable lines are a
    tuple of `(line number, reason)` pairs. A line that opens a fence after up to three spaces is
    skipped, and so is every line until the fence closes, so no fence line is read. A report with no ## Threads section holds no child. A line
    with no text is skipped, and so is a line of the section that is plain prose, that is a line that
    starts, after any leading whitespace, with neither a list marker nor a `#`, holds no backticked thread
    id, and names no lowercase child role. A list marker is a hyphen, an asterisk, a plus, a bullet, or a number
    followed by a dot or a closing parenthesis. A non-blank line that is read, is not prose and is not one
    item of the form - <role> `<thread id>` <seat> is reported by its number. A heading deeper than the section's own or a
    second ## Threads heading while the first is still open is reported by its number and ends the scan. An
    item whose thread id the table would change is reported by its number too. A role that is well formed
    but is not one of CHILD_ROLES is reported by its number, and named with the command that writes it when
    a role of THREADS_WRITERS names it.
    """
    children, unreadable = [], []
    level, fence = 0, None
    for number, line in enumerate(text.splitlines(), 1):
        if fence:
            if fence_close(line, fence):
                fence = None
            continue
        opened = threads_fence_open(line)
        if opened:
            fence = opened
            continue
        heading = HEADING.match(line)
        if heading:
            depth, name = len(heading.group(1)), heading.group(2)
            if (depth, name) == THREADS_SECTION:
                if level:
                    unreadable.append((number, THREADS_REFUSED["again"]))
                    break
                level = depth
            elif depth <= level:
                level = 0
            elif level:
                unreadable.append((number, THREADS_REFUSED["deeper"]))
                break
            continue
        if not level or not line.strip():
            continue
        item = THREADS_ITEM.match(line)
        if not item:
            if not threads_prose(line):
                unreadable.append((number, THREADS_REFUSED["item"]))
            continue
        role, thread, seat = item.groups()
        if clean(thread) != thread:
            unreadable.append((number, THREADS_REFUSED["id"]))
            continue
        if role in THREADS_WRITERS:
            unreadable.append((number, THREADS_REFUSED["writer"].format(role=role, writer=THREADS_WRITERS[role])))
            continue
        if role not in CHILD_ROLES:
            unreadable.append((number, THREADS_REFUSED["role"].format(role=role, roles=one_of(CHILD_ROLES))))
            continue
        children.append({"role": role, "thread": thread, "seat": seat})
    return tuple(children), tuple(unreadable)


def report_threads_refusal(restaurant, dish_id, unreadable):
    """One BrigadeError text naming the item, its report, and every line report_threads could not read."""
    lines = "; ".join(f"line {number} {reason}" for number, reason in unreadable)
    return THREADS_REFUSED_LINE.format(dish=dish_id, name=f"{dish_id}.md", lines=lines)


def thread_binding(row):
    """The identity `write_threads` de-duplicates a row on, as the table stores it."""
    return (row["attempt"], row["role"], stored("threads.tsv", "thread", row["thread"]))


def write_threads(restaurant, dish_id, threads, closing=False, keep_thread=""):
    """The one writer of threads.tsv. Returns True when it wrote a row or closed one.

    `threads` is a sequence of `(role, thread, seat)`. A worker role's attempt is one more than the worker
    rows the item already holds for another thread, and every other role's is that count, or 1 when the
    item holds none. A worker role's row is written open, with its start at this write, and its role is
    worker at attempt 1 and fix worker at every later one. Every other role's row is written closed, with
    its end at this write. A thread id is compared and written as the table stores it, because save_rows
    writes the cell through clean. `closing` writes this stamp into the end of every open worker row of
    the item but one whose stored thread is the stored `keep_thread`. A row whose item, attempt, role,
    and stored thread a row already holds is dropped, so a rerun that finds the same item, attempt, role,
    and thread writes no row. When every row is dropped and nothing closes, no file changes.
    """
    rows = restaurant.rows("threads.tsv")
    mine = [row for row in rows if row["dish"] == dish_id]
    stamp = seam.now()
    keep = stored("threads.tsv", "thread", keep_thread) if keep_thread else ""
    held = {thread_binding(row) for row in mine}
    written = []
    for role, thread, seat in threads:
        thread = stored("threads.tsv", "thread", thread)
        worker = role in WORKER_ROLES
        used = sum(1 for row in mine if row["role"] in WORKER_ROLES and row["thread"] != thread)
        number = used + 1 if worker else max(used, 1)
        if worker:
            role = "worker" if number == 1 else "fix worker"
        if (str(number), role, thread) in held:
            continue
        held.add((str(number), role, thread))
        written.append({"at": stamp, "dish": dish_id, "attempt": number, "role": role, "thread": thread,
                        "seat": seat, "start": stamp if worker else "", "end": "" if worker else stamp})
    closed = False
    if closing:
        for row in mine:
            if row["role"] in WORKER_ROLES and not row["end"] and row["thread"] != keep:
                row["end"] = stamp
                closed = True
    if not written and not closed:
        return False
    restaurant.save_rows("threads.tsv", rows + written)
    return True
