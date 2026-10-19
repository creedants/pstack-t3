#!/usr/bin/env python3

"""Show what one brigade coordinator's agents and sub-agents are doing, as one HTML page.

Opens the coordinator's store and T3 Code's state database read-only.
Prints one self-contained HTML document, or plain lines with --text, or writes either to the file --out names.
The output and the newline after it take at most --max-bytes bytes.
Output that would take more is shortened. It counts the agents, work items, attempt lines, and pull request links it leaves out, and it ends each string it cuts with an ellipsis.

Every string a page emits passes one filter after its parts are joined.
In the HTML document that is each string of the data element. In the plain form it is each line.
The filter drops each character UTF-8 cannot hold and writes each control character, line separator, and paragraph separator as a space.
It then removes each word that holds private text and repeats until no word does.
The first word that stays follows the spaces that start the string, and each later word that stays follows the spaces that stood before it.
A string that still holds private text after 8 passes is emitted empty.
The filter reads each string as written, as the HTML document writes it, and both with their percent escapes decoded once and twice.

Private text is an identifier or a path this run read, as stored and with its own percent escapes decoded once and twice.
Its control characters are read as spaces, and it is compared with each character of both texts in lower case when that is one character.
The identifiers are the thread ids, the sub-agent ids, and the work items' task values in the store and in T3's turn and sub-agent rows.
An identifier that reads as ordinary words is not private text.
That is at most 48 characters of letters and digits in runs joined by single spaces, dots, or hyphens, where a run with both letters and digits is at most 8 characters.
A form of spaces only is not private text either. Neither is an identifier form of fewer than 4 characters.
A non-empty thread-column value is read as stored and with its percent escapes decoded once and twice, and each of those forms is also read without the spaces that start and end it.
A reading that holds a character other than a space is private text even when it reads as ordinary words.
A form of 4 or more characters is private text anywhere in a word.
A shorter form is private text only where no letter, digit, underscore, or hyphen stands directly before or after it.
The paths are the store's directory, the project root the store records, T3 Code's base directory and database, the home directory, and the --out file.
Each path is read as an absolute path, with symbolic links followed, and as given when that is absolute.
A path form of 4 or more characters is private text anywhere in a word.
A path form of fewer than 4 characters is read without the slashes that end it, and a form of slashes only is read as one slash. It is private text only as a whole word.
That word starts with a slash that starts the string or follows one of the characters the last path shape below can follow, and it ends before the next space, quotation mark, or backtick.
The word is also the form when it is the form before one or more of a slash, a closing round, square, curly, or angle bracket, and the marks . , ; : ! ? |.

Private text is also text of one of these shapes, with upper and lower case letters read alike.
A UUID.
One of mcp, thread, node, run, task, run-attempt, provider-turn, provider-session, context-transfer, and context-handoff anywhere in a word, then a colon, then a letter, a digit, an underscore, or a percent sign.
An email address, which is an at sign between a local part and a domain.
The domain is a name that holds a dot before two or more letters. Or it is square brackets around four numbers of 1 to 3 digits joined by dots, or around `IPv6:` and one or more hexadecimal digits, colons, and dots.
The local part is 1 to 62 characters other than a straight double quotation mark between two such marks.
Or it is 1 to 62 characters between two such marks, read from the left with a backslash and the character after it as a pair, where the last pair ends before the closing mark and each such mark among the characters is the second character of a pair.
Or it ends in a character other than a space, an at sign, a round or square bracket, an angle bracket, a straight quotation mark, a comma, a semicolon, a colon, or an ampersand.
`file:` with no letter or digit before it, then a letter, a digit, an underscore, a slash, a backslash, a dot, a tilde, or a percent sign.
A drive path, which is a letter from a to z with no letter or digit before it, then a colon, then a slash or a backslash.
A path that starts the string or follows a space, a quotation mark, a backtick, an opening bracket, an angle bracket, an equals sign, a colon, a comma, a semicolon, or a vertical bar.
Such a path starts with a slash before a character other than a space, a slash, a quotation mark, or a backtick, and it holds a second slash with no space, quotation mark, or backtick before it and a character other than those after it.
It can start with two or more slashes instead when no colon stands before them.
Or it starts with a tilde before a slash or a backslash, with a tilde and a name that starts with a letter or an underscore before one, with $HOME or ${HOME} before one, or with two backslashes before a letter, a digit, an underscore, or a dot.
A path after an opening quotation mark or a backtick is private text up to the mark that closes it.
Any other path, and a path with no mark that closes it, is private text to the end of its word and through each next word that holds a slash or a backslash and does not start with a slash.

Private text is also the request name of a delegated task when that name holds a hyphen.
The request names are the text after the last `delegate-task:` of each identifier this run read, with the identifier's percent escapes decoded once and without the spaces that start and end that text.
A name that is runs of letters and digits joined by single hyphens is private text only as a whole such run, compared in lower case as an identifier is.
A longer such run that holds the name is not the name. Neither is the name after a slash that starts the string or follows one of the characters the last path shape above can follow, such as /code-review.
Any other name with a hyphen is read as it is and with its percent escapes decoded once and twice, each form without the spaces that start and end it. It is compared as a path this run read is.
A form of 4 or more characters is private text anywhere in a word.
A form of fewer than 4 characters is private text only where no letter, digit, underscore, or hyphen stands directly before or after it.
A request name with no hyphen is not private text.

The filter removes a word for no other reason.
The filter leaves a work item id, a summary, a title, the store's name, and a model name as they are unless they hold private text. It leaves a relative path such as src/a.py.
It leaves a word of one segment that starts with a slash, such as /review, unless it holds private text.
A delegated task's own request name of two or more hyphen-separated parts is split into words for its label.
A pull request link is printed only as https://host/owner/repository/pull/number. Userinfo, a query, and a fragment are dropped.
A work item on the page whose nonblank pull request value cannot be converted to such an address, or whose address the filter would change, has no link and is counted in a note.
Different work items can print the same id, summary, state, and link. Each work item that is in flight or has an agent in the window is still listed or counted as its own work item.

--serve runs a local HTTP server on 127.0.0.1 for this store's page and prints its token address. --live-page prints the wrapper that polls the live server for html_render at the given address plus /activity/<token>. The address defaults to the fixed http://127.0.0.1:48731, which does not follow --serve. It accepts an origin such as https://home.example.ts.net:48731. The token lives in activity-token under the store directory.

Patching this module need not reroute the package.
"""

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

import sys as _sys
import types as _types
import uuid as _uuid
from pathlib import Path as _Path
_libdir = _Path(__file__).resolve().parent / "activity_lib"


def _drop_cached_activity(*names):
    for _n in [n for n in _sys.modules if any(n == name or n.startswith(name + ".") for name in names)]:
        del _sys.modules[_n]


_prior_activity = {n: m for n, m in _sys.modules.items() if n == "activity_lib" or n.startswith("activity_lib.")}
_drop_cached_activity("activity_lib")
_pkg = "_activity_ns_" + _uuid.uuid4().hex
_alias = _types.ModuleType(_pkg)
_alias.__path__ = [str(_libdir)]
_sys.modules[_pkg] = _alias
_sys.modules["activity_lib"] = _alias
_bw = _sys.dont_write_bytecode
_sys.dont_write_bytecode = True
try:

    from activity_lib.store import ActivityError, SourceError, GoalRecord, Goal, Unit, Store, STORE_RECORD, STORE_LOCK, UNITS_TABLE, LOG_TABLE, GOALS_TABLE, THREADS_TABLE, STORE_COLUMNS, ADDED_COLUMNS, TABLE_WORDS, RECORD_WORDS, STATE_WORDS, UNKNOWN_STATE, FINISHED_STATES, KINDS, UNGROUPED, UNNAMED, UNASSIGNED, read_table, malformed, record_text, goals_of, read_store, roots_of

    from activity_lib.theme import joined_lines, squeezed, STYLE, RENDERER, STRIP_CSS, LEDGER_CSS, ATTEMPT_CSS, LEDGER_HOOK, STRIP_HOOK

    from activity_lib.t3state import Status, T3_STATUS, OPEN, LIVE, QUIET, Window, Turn, Delegation, Agent, T3, PROJECTION, SUPPORTED_PROJECTION, THREADS, RUNS, SUBAGENTS, METADATA, T3_SHAPE, THREAD_PAYLOAD_KEY, SELECTION_KEYS, NULLABLE, STAMP, T3_DATABASE, BATCH, CHANGED, T3_KINDS, t3_database, open_t3, unreadable, check_shape, check_coordinator, parse_time, text_in, time_in, parse_status, request_name, in_scope, batches, touches, check_parents, same_project, read_t3, selection_of

    from activity_lib.privacy import UUID, OPENS, QUOTES, SEGMENT, PATH, SHAPES, LOCAL, LOCAL_CHARS, DOMAIN, TOKEN_ATOM, TOKEN, OPENS_AT, PIECE, SLASH_WORD, BESIDE, CLOSERS, CLOSES, WORD_END, NEXT_PATH_WORD, WORD, RUN, ORDINARY, ORDINARY_CHARS, ESCAPES, ANCHOR, PASSES, MODEL_CHARS, HOST, PULL, REFUSED, CUT, PATH_TAIL, SLUG, HASH, ROLE, UNIT_SUFFIX, BAR, ENTITIES, UNPRINTED, DRIVERS, OTHER_MODEL, MODEL_SLOTS, OTHER_SLOT, MIXED_SLOT, entities, plain, lowered, ordinary, unescaped, as_entities, views, path_end, without, short, Privacy, privacy_of

    from activity_lib.page import Evidence, Assignment, Span, RoleCount, AttemptFold, Row, Item, LedgerLine, Group, Totals, Hidden, Page, StripGroup, Flight, ROLES, TITLE_CHARS, LABEL_CHARS, NAME_BYTES, ID_BYTES, LABEL_BYTES, SUMMARY_BYTES, MODEL_BYTES, LINK_BYTES, encode, size, clip, count, people, agents_in, union, summary, shown, tally, flight_of, checksum, model_slot, palette_of, span, dur, unit_named, assign, words_of, label_of, stretches, open_turn, status_of, spans_of, seconds_of, tree, with_parents, is_open

    from activity_lib.attempts import attempt_rank, lines_of, attempt_tail, format_attempt, brief_attempt, partition_attempts, spoken, item_of, build_page

    from activity_lib.fitting import KEPT_ITEMS, MAX_GROUPS, MAX_ROWS, MAX_SPANS, COORDINATOR_SPANS, MAX_STRIP, JOINED, FOLDS, families, kept_rows, finished, fold_finished_items, fold_quiet_subagents, keep_finished_items, first, joined, narrow, counted, cut_items, count_line, cap_everything

    from activity_lib.sizing import FRAME_HEIGHT, FRAME_MARGIN, FRAME_FOLDS, STATS_PX, HEADING_PX, OPEN_PX, LEGEND_PX, AXIS_PX, GROUP_PX, ROW_PX, LEDGER_PX, LEDGER_GAP_PX, CHIP_PX, STRIP_PX, FOOT_PX, LINE_PX, FOOT_TEXT, FOOT_CHARS, NOTE_CHARS, COUNT_CHARS, COUNT_LINE_PX, COUNT_GAP_PX, text_lines, count_slots, flight_column_px, fixed_height, ledger_px, height, frame_cuts, framed, fit_lines, say, items_of, notes

    from activity_lib.budget import BUDGET, DEFAULT_HOURS, MIN_HOURS, MAX_HOURS, MAX_BUDGET, FIXED_BUDGET, line_count, limit_ledger, newest_ledger, briefed, within_budget, shortened

    from activity_lib.rendering import WIRE_VERSION, WIRE_STATUS, WIRE_BARS, TEXT_RUNNING, TEXT_QUEUED, TEXT_ITEMS, TEXT_FAILED, wire, render_html, render_text, fit, fit_text

    from activity_lib.server import DEFAULT_PORT, LIVE_REBUILD_SECONDS, LIVE_POLL_MS, TOKEN_FILE_NAME, TOKEN_BYTES, LIVE_CSS, LIVE_FOLD, LIVE_SCRIPT, Origin, Secret, Settings, Pages, origin_of, secret_of, read_valid_token, install_token, LiveServer, live_server, LiveHandler, serve, live_wrapper, render_page

finally:
    _sys.dont_write_bytecode = _bw
    _drop_cached_activity("activity_lib", _pkg)
    _sys.modules.update(_prior_activity)
del _bw, _drop_cached_activity, _pkg, _alias, _libdir, _types, _uuid, _prior_activity

def parser():
    top = argparse.ArgumentParser(prog="activity.py", description=__doc__.split("\n\nPatching this module")[0])
    top.add_argument("--at", help="the coordinator's store directory (default $BRIGADE_DIR)")
    top.add_argument("--hours", type=float, default=DEFAULT_HOURS, help="how many hours back the page looks, from 0.01 to 168, rounded to whole seconds")
    top.add_argument("--text", action="store_true", help="print plain lines instead of the HTML document")
    top.add_argument("--out", help="write the output to this file and print `wrote <file> (<n> bytes)` instead; a file inside T3 Code's directory or the store is refused")
    top.add_argument("--max-bytes", type=int, default=BUDGET, help="the most bytes the output and the newline after it take, from 16000 to 500000")
    top.add_argument("--t3-home", help="T3 Code's base directory (default $T3CODE_HOME, else ~/.t3)")
    top.add_argument("--serve", nargs="?", const=DEFAULT_PORT, default=None, type=int, metavar="PORT",
                     help="run a local HTTP server on 127.0.0.1 for this store's page, rebuilt at most every 10 seconds; PORT defaults to 48731 and 0 picks an ephemeral port. The wrapper sends its token to whatever listens on the port, so on a machine with other local users, do not leave a rendered wrapper polling while the server is stopped")
    top.add_argument("--live-page", nargs="?", const="", default=None, metavar="ORIGIN",
                     help="print the self-contained HTML wrapper that polls the live server every 30 seconds for html_render at the given address plus /activity/<token>; with no value the address is the fixed http://127.0.0.1:48731 and does not follow --serve, with an http:// or https:// origin with a host and no userinfo, path, query, or fragment such as https://home.example.ts.net:48731 it polls that origin; a value with whitespace, a backslash, ?, or #, or with a port that does not parse, is refused; the wrapper holds the token, so never share or commit it")
    return top

def main(argv=None):
    try:
        output = run(sys.argv[1:] if argv is None else argv)
    except ActivityError as error:
        print(f"activity: {error}", file=sys.stderr)
        return error.status
    if output is None:
        return 0
    sys.stdout.buffer.write(output.encode(errors="backslashreplace") + b"\n")
    return 0

def run(argv):
    args = parser().parse_args(argv)
    if not MIN_HOURS <= args.hours <= MAX_HOURS:
        raise ActivityError("--hours must be from 0.01 to 168; pass a number in that range")
    if not BUDGET <= args.max_bytes <= MAX_BUDGET:
        raise ActivityError("--max-bytes must be from 16000 to 500000; pass a number in that range")
    directory = store_dir(args.at, os.environ)
    if args.live_page is not None:
        origin = None if args.live_page == "" else origin_of(args.live_page)
        return live_wrapper(secret_of(directory, DEFAULT_PORT, origin).url)
    if args.serve is not None:
        if not 0 <= args.serve <= 65535:
            raise ActivityError("--serve takes a port from 0 to 65535; 0 picks an ephemeral port and no value means 48731")
        path = t3_database(args.t3_home, os.environ, Path.home())
        serve(Settings(directory, path, args.hours, args.max_bytes), secret_of(directory, args.serve), args.serve)
        return None
    store = read_store(directory)
    path = t3_database(args.t3_home, os.environ, Path.home())
    target = out_file(args.out, path, directory) if args.out else None
    output = render_page(Settings(directory, path, args.hours, args.max_bytes), args.text, args.out, store)
    if target is None:
        return output
    data = output.encode(errors="backslashreplace")
    try:
        target.write_bytes(data)
    except OSError as error:
        raise ActivityError(f"cannot write the --out file ({error.strerror}); pass a path this user can write") from None
    return f"wrote {args.out} ({len(data)} bytes)"

def out_file(flag, database, store):
    """The file --out names, with every symbolic link in its path followed.
    It is refused inside T3's base directory, inside the directory that holds the database, and inside the store.
    It is refused when it is the same file as the database or one of its two side files, which covers a hard link.
    """
    target = Path(os.path.realpath(flag))

    def inside(directory):
        return Path(os.path.realpath(directory)) in (target, *target.parents)

    def same(file):
        try:
            return os.path.samefile(target, file)
        except OSError:
            return False

    if inside(database.parents[1]) or inside(database.parent) or any(same(f"{database}{suffix}") for suffix in ("", "-wal", "-shm")):
        raise ActivityError("--out names a file inside T3 Code's directory or one of its database files; pass a path outside it")
    if inside(store):
        raise ActivityError("--out names a file inside the coordinator's store; pass a path outside it")
    return target

def store_dir(flag, environ):
    chosen = flag or environ.get("BRIGADE_DIR")
    if not chosen:
        raise ActivityError("pass --at <store directory> or set BRIGADE_DIR")
    return Path(chosen)

if __name__ == "__main__":
    sys.exit(main())
