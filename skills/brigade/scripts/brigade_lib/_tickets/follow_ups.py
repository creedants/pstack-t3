import re
from dataclasses import dataclass
from brigade_lib._notes import append_notes, marker, note_rows, read_notes
from brigade_lib._tickets.labels import ActsOn, read_labels
from brigade_lib._tickets.paths import priority_of, quoted_paths
from brigade_lib._tickets.rail import append_ticket
from brigade_lib.shared import now, same_text
from brigade_lib.store import LIVE_TICKET_STATES

@dataclass(frozen=True)
class FollowUps:
    """What a report's follow-ups sections hold. A report with no such section is None, not this.

    Each of `items` is a follow-up, at most one of them becomes a ticket, and its position plus one is the `#<n>` in its ref.
    `asides` are the (reason, text) pairs the command prints and never files.
    """
    items: tuple
    asides: tuple


NO_WORK = ("none", "none required", "none needed", "nothing", "nothing needed", "n/a", "not applicable", "no follow-ups")


def skipped_follow_up_line(line):
    """True when the line's text is `---` or the line is the list item `- ---`.

    Leading and trailing whitespace do not count.
    """
    stripped = line.strip()
    return stripped == "---" or re.fullmatch(r"-\s+---", stripped) is not None


def says_no_work(text):
    """True when the block is one of NO_WORK and nothing more.

    Case does not count. Neither do list markers, the `*`, `_`, and backtick marks around it, or a final period.
    """
    text = re.sub(r"^(?:(?:[-*+]|\d+[.)])\s+)+", "", text.strip())
    return text.strip("*_` ").removesuffix(".").strip("*_` ").casefold() in NO_WORK


def follow_ups(text):
    """Every follow-ups section of a report, or None when no heading says follow-ups.

    A top-level list item or a paragraph is one block, and the indented lines, nested bullets, and fenced code under it
    are part of it. Each block is a follow-up, with three exceptions that are asides. A block that is one of NO_WORK
    and nothing more is one. So is a paragraph directly above a list item, which introduces the list, and a paragraph
    below its section's last list item, which closes the section.

    The text that follows the word on a follow-ups heading line is an aside too, whatever it says. It is the rest of the
    line without leading colons and surrounding whitespace.

    The text of a heading deeper than the open follow-ups heading that does not itself say follow-ups is an aside too,
    whatever it says. It is the line after its opening `#` marks, without surrounding whitespace. Such a heading with no
    text is an aside with no text. A paragraph with only such headings between it and a list item is directly above that item.

    A list item with no text is an aside with no text. It is not the list item a paragraph is directly above, and it is
    not its section's last list item. A paragraph looks past it as it looks past such a heading.

    A line outside a fence whose text is only `---` is not a block. The same holds for a list item `- ---` outside a
    fence. Leading and trailing whitespace do not count. Such a line separates blocks the way a blank line does.
    """
    sections, level, fenced, blank, block = [], 0, "", True, None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            mark = "`"
        elif stripped.startswith("~~~"):
            mark = "~"
        else:
            mark = ""
        fence = mark == fenced if fenced else bool(mark)
        plain = not fence and not fenced
        if fence:
            fenced = "" if fenced else mark
        heading = plain and re.match(r"(#{1,6})(\s|$)", line)
        if heading:
            depth = len(heading.group(1))
            named = re.match(r"#{1,6}\s+follow-?ups?\b", line, re.I)
            if named:
                level = depth
                sections.append([("heading", [re.sub(r"^[\s:]+", "", line[named.end():])])])
            elif depth <= level:
                level = 0
            elif level:
                sections[-1].append(("subheading", [line[depth:]]))
            block, blank = None, True
        elif not level:
            continue
        elif not line.strip():
            blank = plain
        elif plain and skipped_follow_up_line(line):
            blank = True
        else:
            marker = plain and re.match(r"(?:[-*+]|\d+[.)])(?:\s+|$)", line)
            if marker or block is None or plain and blank and not line[0].isspace():
                block = ("item" if marker else "para", [])
                sections[-1].append(block)
            block[1].append(line[marker.end():] if marker else line)
            blank = False
    if not sections:
        return None
    items, asides = [], []
    for blocks in sections:
        blocks = [(kind, " ".join(part.strip() for part in lines).strip()) for kind, lines in blocks]
        blocks = [("bare" if kind == "item" and not text else kind, text) for kind, text in blocks]
        blocks = [(kind, text) for kind, text in blocks if text or kind in ("subheading", "bare")]
        kinds = [kind for kind, _ in blocks] + ["end"]
        last = max((index for index, kind in enumerate(kinds) if kind == "item"), default=len(kinds))
        for index, (kind, text) in enumerate(blocks):
            if kind == "heading":
                asides.append(("text on the heading line", text))
            elif kind == "subheading":
                asides.append(("heading inside the section" if text else "heading inside the section with no text", text))
            elif kind == "bare":
                asides.append(("list item with no text", ""))
            elif says_no_work(text):
                asides.append(("says no work is needed", text))
            elif kind == "para" and next(below for below in kinds[index + 1:] if below not in ("subheading", "bare")) == "item":
                asides.append(("prose that introduces a list", text))
            elif kind == "para" and index > last:
                asides.append(("prose after the last list item", text))
            else:
                items.append(text)
    return FollowUps(tuple(items), tuple(asides))


def follow_up_kind(item_kind, priority):
    """The kind of a follow-up ticket. No kind when its item has none. Else fix at priority urgent or normal, and polish at low."""
    if not item_kind:
        return ""
    return "polish" if priority == "low" else "fix"


def file_follow_ups(restaurant, name, found, write, tracked):
    """At most one waiting ticket per item, for the first marked follow-up whose text is not on the rail.

    A done or dropped ticket counts, so a rerun on the same report after the coordinator dropped one files nothing.
    The ref ends in a position, which is not an identity, so the text is the only key of the rail check, refuse_live_ref
    is not asked, and the one-ticket check reads the report name in the ref.
    Each ticket takes the goal of the report's item, whatever that goal's state, and follow_up_kind of that item's kind.
    A follow-up is marked when marker(text) returns safety, privacy, or correctness. It removes leading whitespace,
    then one contiguous run of *, _, or backtick marks, then leading whitespace again. Case does not count, and
    the marker word must be followed immediately by a colon. At most one marked
    follow-up becomes a ticket: the first whose text is not on the rail, and only when no ticket from this report
    exists yet, in any state.
    Every other follow-up whose text is not on the rail goes to notes.tsv keyed by the tracked paths it quotes.
    """
    restaurant.find("dishes.tsv", name[:-3])
    label = read_labels(restaurant, ActsOn(items=(name[:-3],))).items[name[:-3]]
    kind = follow_up_kind(label.kind, priority_of({"priority": "", "source": "report"}))
    if found is None:
        return f"reports/{name} has no follow-ups section; nothing added"
    aside = [f"not filed, {reason}: {text}" if text else f"not filed, {reason}" for reason, text in found.asides]
    if not found.items:
        return "\n".join([f"reports/{name} lists no follow-ups; nothing added", *aside])
    holders = {}
    for row in sorted(restaurant.rows("rail.tsv"), key=lambda row: row["state"] not in LIVE_TICKET_STATES):
        holders.setdefault(same_text(row["summary"]), f"{row['id']} ({row['state']})")
    taken = re.compile(re.escape(f"{restaurant.dir.name}/reports/{name}") + r"#\d+$")
    has_ticket = any(taken.fullmatch(row.get("ref") or "") for row in restaurant.rows("rail.tsv"))
    item = name[:-3]
    stamp = now()
    seen = set()
    pending = []
    for path, at_item, at, text in read_notes(restaurant):
        seen.add((path, at_item, same_text(text)))
    lines = []
    for number, text in enumerate(found.items, 1):
        ref = f"{restaurant.dir.name}/reports/{name}#{number}"
        key = same_text(text)
        if key in holders:
            lines.append(f"skipped {ref}, same text as {holders[key]}: {text}")
            continue
        marked = marker(text)
        if not marked:
            rows = note_rows(text, item, stamp, tracked)
            if write:
                for path, note_item, at, note_text in rows:
                    note_key = (path, note_item, same_text(note_text))
                    if note_key in seen:
                        if path:
                            lines.append(f"already noted {path} from {ref}: {text}")
                        else:
                            lines.append(f"already noted {ref}, no tracked path: {text}")
                    else:
                        if path:
                            lines.append(f"noted {path} from {ref}: {text}")
                        else:
                            lines.append(f"noted {ref}, no tracked path: {text}")
                        seen.add(note_key)
                        pending.append((path, note_item, at, note_text))
            else:
                for path, note_item, at, note_text in rows:
                    note_key = (path, note_item, same_text(note_text))
                    said = "already noted" if note_key in seen else "would note"
                    if path:
                        lines.append(f"{said} {path} from {ref}: {text}")
                    else:
                        lines.append(f"{said} {ref}, no tracked path: {text}")
                    seen.add(note_key)
            continue
        if not has_ticket:
            if write:
                ident = append_ticket(restaurant, text, "report", ref, paths=quoted_paths(text, tracked), kind=kind, goal=label.goal)
                holders[key] = f"{ident} (waiting)"
                lines.append(f"{ident} added from {ref}: {text}")
            else:
                holders[key] = f"#{number} above"
                lines.append(f"would add from {ref}: {text}")
            has_ticket = True
        else:
            lines.append(f"not filed, {name} takes one ticket per item: {text}")
            rows = note_rows(text, item, stamp, tracked)
            if write:
                for path, note_item, at, note_text in rows:
                    note_key = (path, note_item, same_text(note_text))
                    if note_key in seen:
                        if path:
                            lines.append(f"already noted {path} from {ref}: {text}")
                        else:
                            lines.append(f"already noted {ref}, no tracked path: {text}")
                    else:
                        if path:
                            lines.append(f"noted {path} from {ref}: {text}")
                        else:
                            lines.append(f"noted {ref}, no tracked path: {text}")
                        seen.add(note_key)
                        pending.append((path, note_item, at, note_text))
            else:
                for path, note_item, at, note_text in rows:
                    note_key = (path, note_item, same_text(note_text))
                    said = "already noted" if note_key in seen else "would note"
                    if path:
                        lines.append(f"{said} {path} from {ref}: {text}")
                    else:
                        lines.append(f"{said} {ref}, no tracked path: {text}")
                    seen.add(note_key)
    if write and pending:
        append_notes(restaurant, pending)
    return "\n".join(lines + aside)
