from __future__ import annotations

import re
from pathlib import Path
from .support import FILL, FOLLOW_UP, KIND_CHOICE, RESTAURANT, SLUG, Span, Step, Tape, Token, _COMMAND, _PLACEHOLDER, skill_files

def command_spans(text: str, source: str = "SKILL.md") -> tuple[Span, ...]:
    """Code spans that are `$B ticket add` or `$B fire` command lines, in file order."""
    found: list[Span] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        mark = _fence_mark(line)
        if fence is None:
            if mark is not None:
                fence = mark
                continue
            for raw in _inline_codes(line):
                candidate = raw.strip()
                if _is_command(candidate):
                    found.append(Span(number, candidate, source))
            continue
        if mark is not None and len(mark) >= len(fence):
            fence = None
            continue
        candidate = strip_shell_comment(line).strip()
        if _is_command(candidate):
            found.append(Span(number, candidate, source))
    return tuple(found)


def skill_spans() -> tuple[Span, ...]:
    found: list[Span] = []
    for path in skill_files():
        found.extend(command_spans(path.read_text(), path.name))
    return tuple(found)


def strip_shell_comment(line: str) -> str:
    """Cut from the first space-hash that is outside single and double quotes."""
    quote = ""
    for index, character in enumerate(line):
        if quote:
            if character == quote:
                quote = ""
            continue
        if character in ("'", '"'):
            quote = character
            continue
        if character == "#" and index > 0 and line[index - 1] == " ":
            return line[: index - 1]
    return line


def tapes(spans: tuple[Span, ...]) -> tuple[Tape, ...]:
    """Each span is its own store, except from-report spans, which share one. A kind choice is several skill steps on that store.

    Tokenize each span, fill placeholders from FILL, expand the kind choice, then wrap the argv
    in the setup steps that make that argv succeed. When a kind choice has `--request` or `--ref`
    with the placeholder fill after the flag, those become `A1` upward and `app-7f3-<kind>`.
    A token after the flag that is not that fill stays as written, so a broken flag reaches the parser.
    Any other line keeps FILL, so `<n>` is `1` and `<ref>` is `app-7f3`.
    """
    prepared = tuple((span, _filled_argvs(span)) for span in spans)
    reports = tuple((span, argvs) for span, argvs in prepared if _is_from_report(argvs[0]))
    built: list[Tape] = []
    placed = False
    for span, argvs in prepared:
        if _is_from_report(argvs[0]):
            if not placed:
                built.append(_report_tape(reports))
                placed = True
            continue
        built.append(_one_tape(span, argvs))
    return tuple(built)


def tokenize(text: str) -> tuple[Token, ...]:
    """Drop the leading `$B`. Then scan left to right.

    Whitespace separates tokens. A double quote groups one token and is not part of the argv.
    `[` opens an optional group that is always kept. `]` closes it and is not part of a token.
    Groups do not nest. `<` ... `>` is part of the current token and may contain spaces, so
    `<files and directories the dish will change>` is one token and `G<n>` stays one token.
    A `|` splits a choice after the token is whole. `user|github|<feed>` is three alternatives.
    """
    body = text.strip()
    if body.startswith("$B"):
        body = body[2:].lstrip()
    tokens: list[Token] = []
    index = 0
    length = len(body)
    while index < length:
        while index < length and body[index] in " \t":
            index += 1
        if index >= length:
            break
        if body[index] in "[]":
            index += 1
            continue
        quoted = False
        if body[index] == '"':
            quoted = True
            index += 1
        buf: list[str] = []
        while index < length:
            character = body[index]
            if quoted:
                if character == '"':
                    index += 1
                    break
                if character == "<":
                    index = _take_placeholder(body, index, buf)
                    continue
                buf.append(character)
                index += 1
                continue
            if character in " \t[]":
                break
            if character == '"':
                break
            if character == "<":
                index = _take_placeholder(body, index, buf)
                continue
            buf.append(character)
            index += 1
        raw = "".join(buf)
        if raw:
            tokens.append(Token(_split_bar(raw), quoted))
    return tuple(tokens)


def _fence_mark(line: str) -> str | None:
    stripped = line.lstrip(" \t")
    if not stripped.startswith("`"):
        return None
    size = 0
    while size < len(stripped) and stripped[size] == "`":
        size += 1
    if size < 3 or "`" in stripped[size:]:
        return None
    return stripped[:size]


def _inline_codes(line: str) -> tuple[str, ...]:
    found: list[str] = []
    index = 0
    length = len(line)
    while index < length:
        if line[index] != "`":
            index += 1
            continue
        start = index
        while index < length and line[index] == "`":
            index += 1
        width = index - start
        cursor = index
        while cursor < length:
            if line[cursor] != "`":
                cursor += 1
                continue
            run = cursor
            while cursor < length and line[cursor] == "`":
                cursor += 1
            if cursor - run == width:
                found.append(line[index:run])
                index = cursor
                break
        else:
            continue
    return tuple(found)


def _is_command(text: str) -> bool:
    return bool(_COMMAND.match(text)) and "--" in text


def _take_placeholder(text: str, index: int, buf: list[str]) -> int:
    end = text.find(">", index)
    if end == -1:
        buf.append(text[index])
        return index + 1
    buf.append(text[index : end + 1])
    return end + 1


def _split_bar(token: str) -> tuple[str, ...]:
    parts: list[str] = []
    buf: list[str] = []
    index = 0
    while index < len(token):
        if token[index] == "<":
            end = token.find(">", index)
            if end == -1:
                buf.append(token[index:])
                break
            buf.append(token[index : end + 1])
            index = end + 1
            continue
        if token[index] == "|":
            parts.append("".join(buf))
            buf = []
            index += 1
            continue
        buf.append(token[index])
        index += 1
    parts.append("".join(buf))
    return tuple(parts)


def _fill(token: str, line: int, quoted: bool, source: str = "SKILL.md") -> str:
    if token == "...":
        return FILL["...quoted" if quoted else "...bare"]
    def replace(match: re.Match[str]) -> str:
        key = match.group(0)
        if key not in FILL:
            raise AssertionError(f"{source} line {line}: unknown placeholder {key}")
        return FILL[key]
    return _PLACEHOLDER.sub(replace, token)


def _filled_argvs(span: Span) -> tuple[tuple[str, ...], ...]:
    tokens = tokenize(span.text)
    kind_at = next((index for index, token in enumerate(tokens) if frozenset(token.alternatives) == KIND_CHOICE), None)
    if kind_at is None:
        return (_fill_tokens(tokens, span.line, None, span.source),)
    argvs = []
    for number, kind in enumerate(tokens[kind_at].alternatives, 1):
        argv = list(_fill_tokens(tokens, span.line, (kind_at, kind), span.source))
        _replace(argv, "--ref", f"{FILL['<ref>']}-{kind}", FILL["<ref>"])
        _replace(argv, "--request", f"A{number}", f"A{FILL['<n>']}")
        argvs.append(tuple(argv))
    return tuple(argvs)


def _fill_tokens(tokens: tuple[Token, ...], line: int, kind: tuple[int, str] | None, source: str = "SKILL.md") -> tuple[str, ...]:
    argv = []
    for index, token in enumerate(tokens):
        if kind is not None and index == kind[0]:
            argv.append(kind[1])
            continue
        argv.append(_fill(token.alternatives[0], line, token.quoted, source))
    return tuple(argv)


def _replace(argv: list[str], flag: str, value: str, expected: str) -> None:
    if flag not in argv:
        return
    at = argv.index(flag)
    if at + 1 >= len(argv):
        return
    if argv[at + 1] != expected:
        return
    argv[at + 1] = value


def _option(argv: tuple[str, ...], flag: str, line: int, source: str = "SKILL.md") -> str:
    if flag not in argv:
        raise AssertionError(f"{source} line {line}: missing {flag} in {' '.join(argv)}")
    at = argv.index(flag)
    if at + 1 >= len(argv):
        raise AssertionError(f"{source} line {line}: missing value after {flag} in {' '.join(argv)}")
    return argv[at + 1]


def _is_from_report(argv: tuple[str, ...]) -> bool:
    return argv[:2] == ("ticket", "add") and "--from-report" in argv


def _open_step(line: int, workers: int | None = None, source: str = "SKILL.md") -> Step:
    argv: tuple[str, ...] = ("open", "--project-root", "<project>", "--name", RESTAURANT)
    if workers is not None:
        argv = (*argv, "--workers", str(workers))
    return Step(line, argv, None, source)


def _goal_step(line: int, source: str = "SKILL.md") -> Step:
    return Step(
        line,
        ("goal", "add", "--name", "Skill line", "--purpose", "Hold the ticket", "--done", "The ticket is filed"),
        "G1\n",
        source,
    )


def _one_tape(span: Span, argvs: tuple[tuple[str, ...], ...]) -> Tape:
    if len(argvs) > 1:
        return _kind_tape(span.line, argvs, span.source)
    argv = argvs[0]
    if argv[0] == "fire":
        return _fire_tape(span.line, argv, span.source)
    if argv[:2] == ("ticket", "add"):
        return _plain_add_tape(span.line, argv, span.source)
    raise AssertionError(f"{span.source} line {span.line}: no tape for {' '.join(argv)}")


def _kind_tape(line: int, argvs: tuple[tuple[str, ...], ...], source: str = "SKILL.md") -> Tape:
    """The setup ticket prints T1. Each expanded command prints the next id."""
    steps = [_open_step(line, None, source)]
    if any("--goal" in argv for argv in argvs):
        steps.append(_goal_step(line, source))
    steps.append(Step(line, ("ticket", "add", "--summary", "Skill line request", "--kind", "fix"), "T1\n", source))
    for index, argv in enumerate(argvs, 1):
        steps.append(Step(line, argv, f"T{index + 1}\n", source))
    return Tape(False, tuple(steps))


def _fire_tape(line: int, argv: tuple[str, ...], source: str = "SKILL.md") -> Tape:
    """Create every ticket id the line names, including a gap id next_id would otherwise skip.

    A missing or malformed `--tickets` fails with the line number.
    """
    raw = _option(argv, "--tickets", line, source)
    named = raw.split(",")
    for ident in named:
        if not ident.startswith("T") or not ident[1:].isdigit():
            raise AssertionError(f"{source} line {line}: bad ticket id {ident} in {' '.join(argv)}")
    if "--ride-along" in argv:
        ride_raw = _option(argv, "--ride-along", line, source)
        ride = set(ride_raw.split(",")) if ride_raw else set()
        for ident in ride:
            if not ident.startswith("T") or not ident[1:].isdigit():
                raise AssertionError(f"{source} line {line}: bad ticket id {ident} in {' '.join(argv)}")
    else:
        ride = set()
    if "--kind" in argv:
        kind = _option(argv, "--kind", line, source)
    else:
        kind = "fix"
    lead = named[0]
    highest = max(int(ident[1:]) for ident in named)
    steps = [_open_step(line, None, source)]
    unused = 0
    for number in range(1, highest + 1):
        ident = f"T{number}"
        if ident == lead:
            ticket = ("ticket", "add", "--kind", kind, "--summary", "Lead ticket")
        elif ident in ride:
            ticket = ("ticket", "add", "--kind", "fix", "--priority", "low", "--summary", "Ride along")
        else:
            unused += 1
            summary = "Unused id" if unused == 1 else f"Unused {ident}"
            ticket = ("ticket", "add", "--kind", "fix", "--summary", summary)
        steps.append(Step(line, ticket, f"T{number}\n", source))
    if "--paths" in argv:
        expect = f"D1 (lease L1 held by {SLUG}/D1)\n"
    else:
        expect = "D1\n"
    steps.append(Step(line, argv, expect, source))
    return Tape("--paths" in argv, tuple(steps))


def _plain_add_tape(line: int, argv: tuple[str, ...], source: str = "SKILL.md") -> Tape:
    steps = [_open_step(line, None, source)]
    if "--goal" in argv:
        steps.append(_goal_step(line, source))
    steps.append(Step(line, argv, "T1\n", source))
    return Tape(False, tuple(steps))


def _report_tape(items: tuple[tuple[Span, tuple[tuple[str, ...], ...]], ...]) -> Tape:
    """From-report spans share one store.

    Each report stem is fired once, in numeric dish order, with no paths. Dishes from 1 through
    the highest stem exist, so a lone D2 still has D1. The store opens with workers for the highest
    dish. A dry-run step does not take a ticket id. The first real add is the next id after those lead tickets.
    """
    parsed: list[tuple[Span, tuple[tuple[str, ...], ...], str, int]] = []
    for span, argvs in items:
        raw = _option(argvs[0], "--from-report", span.line, span.source)
        stem = Path(raw).stem
        if not stem.startswith("D") or not stem[1:].isdigit():
            raise AssertionError(f"{span.source} line {span.line}: bad report {raw} in {' '.join(argvs[0])}")
        parsed.append((span, argvs, stem, int(stem[1:])))
    highest = max(number for _, _, _, number in parsed)
    first = items[0][0]
    line = first.line
    source = first.source
    steps = [_open_step(line, highest, source)]
    for number in range(1, highest + 1):
        steps.append(Step(line, ("ticket", "add", "--kind", "fix", "--summary", f"Lead {number}"), f"T{number}\n", source))
        steps.append(Step(
            line,
            ("fire", "--tickets", f"T{number}", "--station", "bug-fix", "--summary", f"Dish {number}"),
            f"D{number}\n",
            source,
        ))
    next_ticket = highest + 1
    item = _follow_up_item()
    for span, argvs, _, _ in parsed:
        argv = argvs[0]
        name = Path(_option(argv, "--from-report", span.line, span.source)).name
        ref = f"{SLUG}/reports/{name}#1"
        if "--dry-run" in argv:
            expect = f"would add from {ref}: {item}\n"
        else:
            expect = f"T{next_ticket} added from {ref}: {item}\n"
            next_ticket += 1
        steps.append(Step(span.line, argv, expect, span.source))
    return Tape(False, tuple(steps))


def _follow_up_item() -> str:
    for line in FOLLOW_UP.splitlines():
        if line.startswith("- "):
            return line[2:]
    raise AssertionError("FOLLOW_UP has no bullet")

