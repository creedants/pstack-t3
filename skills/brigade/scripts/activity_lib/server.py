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
from activity_lib.store import ActivityError, read_store, roots_of
from activity_lib.theme import joined_lines, squeezed
from activity_lib.t3state import Window, check_coordinator, check_shape, open_t3, read_t3, unreadable
from activity_lib.privacy import privacy_of
from activity_lib.attempts import build_page
from activity_lib.sizing import FRAME_HEIGHT, FRAME_MARGIN
from activity_lib.budget import BUDGET
from activity_lib.rendering import fit, fit_text


DEFAULT_PORT = 48731


LIVE_REBUILD_SECONDS = 10


LIVE_POLL_MS = 30000


TOKEN_FILE_NAME = "activity-token"


TOKEN_BYTES = 32


@dataclass(frozen=True)
class Origin:
    text: str


@dataclass(frozen=True)
class Secret:
    token: bytes
    route: bytes
    url: str
    given_origin: bool = False


@dataclass(frozen=True)
class Settings:
    store: Path
    database: Path
    hours: float
    max_bytes: int


@dataclass(frozen=True)
class Pages:
    body: bytes
    built: float
    lock: threading.Lock = field(default_factory=threading.Lock)


def render_page(settings, text=False, out=None, store=None):
    """The page output for one store. run() and each live view request share it, so both filter private text alike. run() passes the store it already read, keeping its refusal order."""
    if store is None:
        store = read_store(settings.store)
    path = settings.database
    now = time.time()
    window = Window(now - round(settings.hours * 3600), now)
    connection = open_t3(path)
    try:
        check_shape(connection)
        check_coordinator(connection, store.coordinators)
        t3 = read_t3(connection, window, roots_of(store), store.coordinators)
    except sqlite3.Error as error:
        raise unreadable(error, path) from None
    finally:
        connection.close()
    privacy = privacy_of(store, t3, (settings.store, path, path.parents[1], Path.home(), *((out,) if out else ())))
    page = build_page(store, t3, window, privacy)
    budget = settings.max_bytes - len("\n")
    frame = FRAME_HEIGHT - FRAME_MARGIN if settings.max_bytes == BUDGET else None
    return fit_text(page, budget, privacy.clean) if text else fit(page, budget, privacy.clean, frame)[1]


LIVE_CSS = joined_lines("""
    .livebar{display:flex;gap:8px;align-items:center;margin-bottom:6px;font:11px var(--font-sans);color:var(--muted-foreground)}
    .livebar button{font:inherit;color:var(--foreground);background:var(--muted);border:1px solid var(--border);border-radius:4px;padding:1px 8px;cursor:pointer}
""")


LIVE_FOLD = squeezed("""
    const openState = {};
    const attemptState = window.attemptState || (window.attemptState = {});
    let appliers = [];
    const setAll = (open) => { for (const a of appliers) { openState[a.fold] = open; a.apply(); } };
    function fold() {
      const lanes = document.querySelector('#o .tl');
      if (!lanes) return;
      appliers = [];
      const groups = [];
      let g = null;
      for (const n of [...lanes.children]) {
        if (n.classList.contains('grp')) { g = {head: n, rows: []}; groups.push(g); }
        else if (g && n.classList.contains('row')) g.rows.push(n);
      }
      const seen = {};
      for (const {head, rows} of groups) {
        const id = (head.querySelector('.one') || {}).textContent || head.textContent;
        const block = head.querySelector('.ldg details');
        const key = (block && block.getAttribute('data-item')) || id;
        const nth = (seen[id] = (seen[id] || 0) + 1);
        const fold = id + '\\0' + nth;
        const collapsed = head.getAttribute('data-collapsed');
        const state = (head.querySelector(':scope>b.chip') || {}).textContent || '';
        const byDefault = !collapsed && !/merged|dropped|done/.test(state);
        const caret = document.createElement('span');
        caret.style.cssText = 'flex:none;width:1em;color:var(--muted-foreground)';
        head.prepend(caret);
        head.style.cursor = 'pointer';
        const apply = () => {
          const open = fold in openState ? openState[fold] : byDefault;
          caret.textContent = open ? '\u25BE' : '\u25B8';
          const attemptOpen = !!attemptState[key];
          if (block) block.open = attemptOpen;
          for (const r of rows) { const earlier = r.classList.contains('earlier'); r.style.display = !open ? 'none' : (earlier && !attemptOpen ? 'none' : ''); }
          const l = head.querySelector('.ldg');
          if (l) l.style.display = open ? '' : 'none';
        };
        head.onclick = (e) => { if (e.target.closest('a,summary,details')) return; openState[fold] = !(fold in openState ? openState[fold] : byDefault); apply(); };
        apply();
        appliers.push({fold, key, apply});
      }
      window.refreshAttempt = (itemId) => { for (const a of appliers) { if (a.key === itemId) a.apply(); } };
    }
""")


LIVE_SCRIPT = squeezed("""
    const status = (t) => { document.getElementById('live').textContent = t; };
    async function tick() {
      try {
        const r = await fetch(LIVE_URL, {cache: 'no-store'});
        if (!r.ok) throw new Error('server answered ' + r.status);
        const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
        const scripts = [...doc.querySelectorAll('script:not([type])')];
        document.getElementById('s').textContent = doc.querySelector('style').textContent + '.tl>.grp{flex-wrap:wrap}';
        document.getElementById('d').textContent = doc.getElementById('d').textContent;
        document.getElementById('o').replaceChildren();
        for (const s of scripts) new Function(s.textContent)();
        fold();
        status('Live. Updates every 30 seconds. Last update ' + new Date().toLocaleTimeString() + '.');
      } catch (e) { status('Live updates paused: the local activity server is not reachable (' + e.message + '). Last good view stays below.'); }
    }
    document.getElementById('ex').onclick = () => setAll(true);
    document.getElementById('co').onclick = () => setAll(false);
    tick();
    setInterval(tick, LIVE_EVERY);
""")


def origin_of(value):
    """The origin for --live-page. A value with whitespace or backslash or ? or # is refused. A port that does not parse is refused. The rest must parse as http:// or https:// with a host and no userinfo, path, query, or fragment."""
    bad = "--live-page takes an http:// or https:// origin with no userinfo, path, query, or fragment; pass one such as https://home.example.ts.net:48731, or omit it to use http://127.0.0.1:48731"
    if any(char.isspace() for char in value) or "\\" in value or "?" in value or "#" in value:
        raise ActivityError(bad)
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ActivityError(bad) from None
    if parts.netloc.endswith(":"):
        raise ActivityError(bad)
    if parts.scheme not in ("http", "https"):
        raise ActivityError(bad)
    if not host:
        raise ActivityError(bad)
    if parts.username is not None or parts.password is not None:
        raise ActivityError(bad)
    if parts.path or parts.query or parts.fragment:
        raise ActivityError(bad)
    return Origin(value)


def secret_of(directory, port, origin=None):
    """The live view's secret for one store. A fresh token holds TOKEN_BYTES bytes from secrets. It lives in <store>/activity-token, a file created mode 0600 and reused across restarts while it stays valid, with surrounding ASCII whitespace ignored, and replaced otherwise."""
    path = directory / TOKEN_FILE_NAME
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fd = None
    except OSError as error:
        raise ActivityError(f"cannot create the live view's token file ({error.strerror}); check its permissions and run this again") from None
    if fd is not None:
        text = secrets.token_urlsafe(TOKEN_BYTES)
        with os.fdopen(fd, "w") as token_file:
            token_file.write(text)
        token = text.encode("ascii")
        if origin is None:
            return Secret(token, b"/activity/" + token, f"http://127.0.0.1:{port}/activity/{text}")
        return Secret(token, b"/activity/" + token, f"{origin.text}/activity/{text}", True)
    text = read_valid_token(path)
    if text:
        token = text.encode("ascii")
        if origin is None:
            return Secret(token, b"/activity/" + token, f"http://127.0.0.1:{port}/activity/{text}")
        return Secret(token, b"/activity/" + token, f"{origin.text}/activity/{text}", True)
    text = secrets.token_urlsafe(TOKEN_BYTES)
    install_token(path, text)
    token = text.encode("ascii")
    if origin is None:
        return Secret(token, b"/activity/" + token, f"http://127.0.0.1:{port}/activity/{text}")
    return Secret(token, b"/activity/" + token, f"{origin.text}/activity/{text}", True)


def read_valid_token(path):
    """The existing token when its file is a private regular file holding a generated token, with surrounding ASCII whitespace ignored, else an empty string."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return ""
    try:
        done = os.fstat(fd)
        if not stat.S_ISREG(done.st_mode):
            return ""
        if done.st_uid != os.getuid():
            return ""
        if stat.S_IMODE(done.st_mode) != 0o600:
            return ""
        raw = b""
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            raw += chunk
            if len(raw) > 4096:
                return ""
        try:
            text = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", text or ""):
            return ""
        return text
    except OSError:
        return ""
    finally:
        os.close(fd)


def install_token(path, text):
    """Write a replacement token through a private temporary file, then move it over the token path."""
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent)
    except OSError as error:
        raise ActivityError(f"cannot write the live view's token file ({error.strerror}); check its permissions and run this again") from None
    try:
        with os.fdopen(tmp_fd, "w") as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except OSError as error:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise ActivityError(f"cannot write the live view's token file ({error.strerror}); check its permissions and run this again") from None


class LiveServer(http.server.ThreadingHTTPServer):
    """The HTTP server for one store's live view. It binds without the hostname lookup in HTTPServer.server_bind."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def live_server(settings, secret, port):
    """The HTTP server for one store's live view. It binds 127.0.0.1 only."""
    server = LiveServer(("127.0.0.1", port), LiveHandler)
    server.daemon_threads = True
    server.live_settings = settings
    server.live_secret = secret
    server.live_pages = Pages(body=b"", built=0.0)
    return server


class LiveHandler(http.server.BaseHTTPRequestHandler):
    """One live view request. GET at the secret route answers the current page. GET at any other path answers 404. Only GET and OPTIONS are answered."""

    def live_send(self, code, kind, body):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.live_send(204, "text/plain; charset=utf-8", b"")

    def do_GET(self):
        secret = self.server.live_secret
        request = self.path.split("?", 1)[0].encode("ascii", "backslashreplace")
        if not secrets.compare_digest(request, secret.route):
            self.live_send(404, "text/plain; charset=utf-8", b"")
            return
        pages = self.server.live_pages
        with pages.lock:
            pages = self.server.live_pages
            now = time.time()
            if pages.body and now - pages.built <= LIVE_REBUILD_SECONDS:
                body = pages.body
            else:
                try:
                    body = render_page(self.server.live_settings).encode(errors="backslashreplace")
                except ActivityError as error:
                    self.live_send(503, "text/plain; charset=utf-8", f"activity: {error}".encode(errors="backslashreplace"))
                    return
                self.server.live_pages = replace(pages, body=body, built=now)
        self.live_send(200, "text/html; charset=utf-8", body)

    def log_message(self, *args):
        pass


def serve(settings, secret, port):
    """Run the live view server for one store until interrupted. It prints the token address so a wrapper can embed it."""
    server = live_server(settings, secret, port)
    actual = server.server_address[1]
    if actual != port and not secret.given_origin:
        secret = replace(secret, url=f"http://127.0.0.1:{actual}{secret.route.decode('ascii')}")
        server.live_secret = secret
    print(secret.url, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def live_wrapper(url):
    """The self-contained wrapper that polls one live view server and folds its page. It holds the token, so it must never be shared or committed."""
    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width,initial-scale=1\">"
        f"<title>Agent activity live</title><style>{LIVE_CSS}</style>"
        "<div class=livebar><button id=ex>Expand all</button><button id=co>Collapse all</button>"
        "<span id=live>Connecting to the live activity server...</span></div>"
        "<style id=s></style><div id=o></div><script type=application/json id=d></script>"
        f"<script>const LIVE_URL={json.dumps(url)};const LIVE_EVERY={LIVE_POLL_MS};{LIVE_FOLD}{LIVE_SCRIPT}</script>")
