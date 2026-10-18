import argparse
import enum
import fcntl
import http.server
import json
import os
import re
import secrets
import socketserver
import sqlite3
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import quote, unquote, urlsplit
from activity_lib.t3state import Status, T3_KINDS, request_name


DRIVERS = frozenset({"claudeAgent", "codex", "grok", "opencode", "cursor"})


OTHER_MODEL = "other model"


MODEL_SLOTS = 5


OTHER_SLOT = 6


MIXED_SLOT = 0


UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"


OPENS = r"(?:^|(?<=[\s\"'`“”‘’(\[{<>=:,;|]))"


QUOTES = "\"'`“”‘’"


SEGMENT = rf"[^\s/{QUOTES}]"


PATH = rf"(?:{OPENS}(?:(?:/|(?<!:)/{{2,}})(?={SEGMENT}+/[^\s{QUOTES}])|~(?:[^\W\d][\w.-]*)?[/\\]|\$(?:HOME|\{{HOME\}})[/\\]|\\\\[\w.])|(?<![^\W_])[a-z]:[/\\])"


SHAPES = re.compile(rf"(?P<path>{PATH})|{UUID}|(?:{'|'.join(T3_KINDS)}):[\w%]|(?<![^\W_])file:(?=[\w/\\.~%])", re.IGNORECASE)


LOCAL = re.compile(r"(?:[^\s@<>()\[\]\"',;:&]+|\"[^\"]+\"|\"(?:\\.|[^\"\\])+\")\Z", re.DOTALL)


LOCAL_CHARS = 64


DOMAIN = re.compile(r"[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}|\[(?:\d{1,3}(?:\.\d{1,3}){3}|IPv6:[0-9A-Fa-f:.]+)\]", re.IGNORECASE)


TOKEN_ATOM = r"[^\W_]"


TOKEN = rf"{TOKEN_ATOM}+(?:-{TOKEN_ATOM}+)+"


OPENS_AT = re.compile(OPENS)


PIECE = re.compile(rf"{TOKEN_ATOM}+|/")


SLASH_WORD = re.compile(rf"{OPENS}/[^\s{QUOTES}]*")


BESIDE = r"[\w-]"


CLOSERS = "/)]}>.,;:!?|"


CLOSES = {'"': '"', "'": "'", "`": "`", "“": "”", "‘": "’"}


WORD_END = re.compile(r"\S*")


NEXT_PATH_WORD = re.compile(r"\s+(?!/)\S*[/\\]\S*")


WORD = re.compile(r"\S+")


RUN = r"(?:[^\W\d_]+|\d+|[^\W_]{1,8})"


ORDINARY = re.compile(rf"{RUN}(?:[ .-]{RUN})*")


ORDINARY_CHARS = 48


ESCAPES = re.compile("(?:%[0-9A-Fa-f]{2})+")


ANCHOR = 4


PASSES = 8


MODEL_CHARS = 48


HOST = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")


PULL = re.compile(r"/(?!\.+/)([A-Za-z0-9._-]+)/(?!\.+/)([A-Za-z0-9._-]+)/pull/[0-9]+")


REFUSED, CUT = "refused", "cut"


PATH_TAIL = re.compile(r"/root/(?:\S*/)?([A-Za-z0-9_-]+)")


SLUG = re.compile(r"[a-z0-9.]+(?:-[a-z0-9.]+)+")


HASH = re.compile(r"(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,}")


ROLE = re.compile(r"Act as the (.+?) sub-agent")


UNIT_SUFFIX = re.compile(r"(.+?)[a-z][0-9]*")


BAR = {
    Status.RUNNING: Status.RUNNING, Status.QUEUED: Status.QUEUED,
    Status.WAITING: Status.DONE, Status.DONE: Status.DONE,
    Status.FAILED: Status.FAILED, Status.STOPPED: Status.STOPPED, Status.UNKNOWN: Status.UNKNOWN,
}


ENTITIES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"), ("\\", "&#92;"))


UNPRINTED = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def entities(text):
    text = UNPRINTED.sub(" ", text)
    for character, entity in ENTITIES:
        text = text.replace(character, entity)
    return text


def plain(text):
    """text without a character UTF-8 cannot hold, and with one space for each character entities() writes as a space."""
    return UNPRINTED.sub(" ", text.encode("utf-8", "ignore").decode())


def lowered(text):
    """text in lower case, one character for each character of text."""
    low = text.lower()
    return low if len(low) == len(text) else "".join(char.lower() if len(char.lower()) == 1 else char for char in text)


def ordinary(text):
    """True for at most 48 characters of letters and digits in runs joined by single spaces, dots, or hyphens, where a run that holds both letters and digits is at most 8 characters."""
    return len(text) <= ORDINARY_CHARS and ORDINARY.fullmatch(text) is not None


def unescaped(text, where=None):
    """text with each run of percent escapes decoded once, and for each character of the result the span of the source it came from.
    where gives that span for each character of text. Without it, text is the source.
    """
    where = [(at, at + 1) for at in range(len(text))] if where is None else where
    parts, origin, at = [], [], 0
    for run in ESCAPES.finditer(text):
        decoded = bytes.fromhex(run.group().replace("%", "")).decode("utf-8", "replace")
        parts += [text[at:run.start()], decoded]
        origin += where[at:run.start()] + [(where[run.start()][0], where[run.end() - 1][1])] * len(decoded)
        at = run.end()
    return "".join([*parts, text[at:]]), origin + where[at:]


def as_entities(text):
    """entities(text) for a text plain() returned, and for each character of it the span of text it came from."""
    written = dict(ENTITIES)
    parts = [written.get(char, char) for char in text]
    return "".join(parts), [(at, at + 1) for at, part in enumerate(parts) for _ in part]


def views(text):
    """The forms of text the filter reads: as written and as entities() writes it, each also with its percent escapes decoded once and twice."""
    for view, where in ((text, None), *((as_entities(text),) if any(char in text for char, _ in ENTITIES) else ())):
        yield view, where
        for _ in range(2):
            if not ESCAPES.search(view):
                break
            view, where = unescaped(view, where)
            yield view, where


def path_end(text, match):
    """Where the path that starts at match ends. After an opening quotation mark or a backtick it ends at the mark that closes it.
    With no such pair it ends with its word, or with the last of the words after it that each hold a slash or a backslash and do not start with a slash.
    """
    quote = text[match.start() - 1:match.start()]
    close = text.find(CLOSES[quote], match.end()) if quote in CLOSES else -1
    if close >= 0:
        return close + 1
    end = WORD_END.match(text, match.end()).end()
    more = NEXT_PATH_WORD.match(text, end)
    while more:
        end = more.end()
        more = NEXT_PATH_WORD.match(text, end)
    return end


def without(text, spans):
    """text without each word that a span touches. The first word that stays follows the spaces that start text.
    Each later word that stays follows the spaces that stood before it. The spaces that end text stay when its last word stays.
    """
    marked = bytearray(len(text))
    for start, end in spans:
        marked[start:end] = b"\x01" * (end - start)
    words = list(WORD.finditer(text))
    keeps = [1 not in marked[word.start():word.end()] for word in words]
    if True not in keeps:
        return ""
    parts = [text[:words[0].start()]]
    for index, word in enumerate(words):
        if keeps[index]:
            parts += [text[words[index - 1].end():word.start()] if len(parts) > 1 else "", word.group()]
    return "".join(parts) + (text[words[-1].end():] if keeps[-1] else "")


class Privacy:
    """The one filter of both output forms. The module docstring states what it removes.
    clean() is the filter. encode() passes it each string of the document's data, and render_text passes it each line.
    field(), model(), and link() shape one value of the store or of T3 for a page, and each calls clean().
    """

    def __init__(self, identifiers=(), paths=(), requests=(), cells=()):
        """index holds each form of 4 or more characters of an identifier that does not read as ordinary words, of a path, of a request name with a hyphen that is not one TOKEN, and of a thread-column value that holds a character other than a space.
        roots holds each shorter form of a path that is not blank, in lower case, without the slashes that end it. A form of slashes only is `/`.
        names holds each request name that is one TOKEN, in lower case.
        shorts matches, in its one group, each shorter form of a request name with a hyphen that is not one TOKEN, and each shorter form of a thread-column value that holds a character other than a space, in lower case, where no BESIDE character is directly before or after it. It is None when there is no such form.
        A request name, and each form of one that is not one TOKEN, is read without the spaces that start and end it. A request name with no hyphen is in none of the four.
        A thread-column value is read as stored and with its percent escapes decoded once and twice, and each of those forms is also read without the spaces that start and end it. ordinary() does not exempt it. A form of spaces only is in none of the four.
        """
        index, roots, shorts = {}, set(), set()
        requests = {plain(value).strip() for value in requests if "-" in value}
        tokens = {value for value in requests if re.fullmatch(TOKEN, value)}
        self.names = frozenset(map(lowered, tokens))
        known = (*((plain(value), False) for value in identifiers), *((plain(value), True) for value in paths))
        for value, path in known:
            for _ in range(3):
                low = lowered(value)
                if len(low) >= ANCHOR and low.strip() and (path or not ordinary(value)):
                    index.setdefault(low[:ANCHOR], set()).add(low)
                elif path and low.strip():
                    roots.add(low.rstrip("/") or "/")
                value = unescaped(value)[0]
        for value in requests - tokens:
            for _ in range(3):
                low = lowered(value).strip()
                if len(low) >= ANCHOR:
                    index.setdefault(low[:ANCHOR], set()).add(low)
                else:
                    shorts.add(low)
                value = unescaped(value)[0]
        for value in map(plain, cells):
            for _ in range(3):
                low = lowered(value)
                for form in (low, low.strip()):
                    if form.strip() and len(form) >= ANCHOR:
                        index.setdefault(form[:ANCHOR], set()).add(form)
                    elif form.strip():
                        shorts.add(form)
                value = unescaped(value)[0]
        self.roots = frozenset(roots)
        self.index = {anchor: tuple(found) for anchor, found in index.items()}
        self.shorts = re.compile(rf"(?<!{BESIDE})(?=({'|'.join(map(re.escape, sorted(shorts, key=len, reverse=True)))})(?!{BESIDE}))") if shorts else None
        self.cache = {}

    def found(self, text):
        """The spans of text that hold an indexed form, a token that is one of names, a form shorts matches, or a word that is one of roots, each in any case, or text of a private shape."""
        def name_spans(low):
            at = 0
            length = len(low)
            while at < length:
                piece = PIECE.match(low, at)
                if piece is None:
                    at += 1
                    continue
                if piece.group() == "/":
                    at = piece.end()
                    continue
                start, end = piece.span()
                while end < length and low[end] == "-":
                    segment = PIECE.match(low, end + 1)
                    if segment is None or segment.group() == "/":
                        break
                    end = segment.end()
                at = end
                if end == piece.end():
                    continue
                slash = start - 1
                if slash >= 0 and low[slash] == "/" and OPENS_AT.match(low, slash):
                    continue
                if low[start:end] in self.names:
                    yield start, end

        low = lowered(text)
        for at in range(len(low) - ANCHOR + 1 if self.index else 0):
            for form in self.index.get(low[at:at + ANCHOR], ()):
                if low.startswith(form, at):
                    yield at, at + len(form)
        if self.names and "-" in low:
            yield from name_spans(low)
        for match in self.shorts.finditer(low) if self.shorts else ():
            yield match.span(1)
        for match in SLASH_WORD.finditer(low) if self.roots else ():
            word = match.group()
            while word not in self.roots and len(word) > 1 and word[-1] in CLOSERS:
                word = word[:-1]
            if word in self.roots:
                yield match.start(), match.start() + len(word)
        covered = 0
        for match in SHAPES.finditer(text):
            if match.start() >= covered:
                covered = path_end(text, match) if match.lastgroup == "path" else match.end()
                yield match.start(), covered
        at = text.find("@")
        while at >= 0:
            local, domain = LOCAL.search(text, max(0, at - LOCAL_CHARS), at), DOMAIN.match(text, at + 1)
            if local and domain:
                yield local.start(), domain.end()
            at = text.find("@", at + 1)

    def private(self, text):
        """The spans of text that found() gives for any of views(text)."""
        return [(start, end) if where is None else (where[start][0], where[end - 1][1])
                for view, where in views(text) for start, end in self.found(view)]

    def clean(self, text):
        """plain(text) without each word that holds private text, repeated until no word does. A text that still holds some after 8 passes becomes empty."""
        if text not in self.cache:
            kept = plain(text)
            for _ in range(PASSES):
                spans = self.private(kept)
                if not spans:
                    break
                kept = without(kept, spans)
            else:
                kept = "" if self.private(kept) else kept
            self.cache[text] = kept
        return self.cache[text]

    def field(self, text):
        """The first line of text with single spaces between its words, as clean() leaves it."""
        line = (text.strip().splitlines() or [""])[0]
        while True:
            tidy = " ".join(self.clean(line).split())
            if tidy == line:
                return tidy
            line = tidy

    def model(self, value):
        """value as field() leaves it, without one leading `<a key of DRIVERS>/`, cut to 48 characters. OTHER_MODEL when nothing is left."""
        driver, slash, rest = value.partition("/")
        name = self.field(rest if slash and driver in DRIVERS else value)
        return short(name, MODEL_CHARS) or OTHER_MODEL

    def link(self, value):
        """The pull request address of value and no reason, or no address and REFUSED. A blank value gives neither.
        The address is https, the host, and the path /owner/repository/pull/number. Userinfo, a query, and a fragment are dropped.
        A value in another form, a value with a port, and an address clean() would change are refused.
        """
        if not value.strip():
            return "", ""
        try:
            parts = urlsplit(value)
            host, port = parts.hostname or "", parts.port
        except ValueError:
            return "", REFUSED
        if parts.scheme != "https" or port is not None or not HOST.fullmatch(host) or not PULL.fullmatch(parts.path):
            return "", REFUSED
        address = f"https://{host}{parts.path}"
        return (address, "") if self.clean(address) == address else ("", REFUSED)


def privacy_of(store, t3, paths=()):
    """The filter for one run. Its identifiers are the thread ids, sub-agent ids, and task values of the store and of T3's rows.
    Its paths are the project root the store records and the given paths.
    The non-empty values in the thread column are not identifiers.
    Its request names are the names request_name() reads from those identifiers and from those values.
    """
    ids = {*store.coordinators, *t3.ids}
    for unit in store.units:
        ids.update((unit.worker, unit.task, *unit.earlier_workers))
    cells = tuple(row["thread"] for row in store.threads if row["thread"])
    # A relative path as given can be one ordinary word, which a page may hold for another reason.
    places = {form for path in (store.project_root, *map(str, paths)) if path
              for form in (os.path.abspath(path), os.path.realpath(path), *((path,) if os.path.isabs(path) else ()))}
    return Privacy(ids, places, filter(None, map(request_name, (*ids, *cells))), cells)


def short(text, limit):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
