import fcntl
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from brigade_lib import seam
from brigade_lib._store.vocab import ADDED_COLUMNS, ADMIN_DIR, CLAIM_WAIT_SECONDS, PREFIX, SNAPSHOT_CHUNK, TABLES
from brigade_lib.shared import BrigadeError, MalformedTable, slug, stored

def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with seam.tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp") as handle:
        try:
            handle.write(text)
        except BaseException:
            handle.close()
            os.unlink(handle.name)
            raise
    os.replace(handle.name, path)


def is_timestamp(value):
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return "T" in value


def parse_row(table, line):
    """One complete line of a table as a row, or None when its field count or timestamp is wrong."""
    header = TABLES[table]
    fields = line.split("\t")
    missing = len(header) - len(fields)
    if not 0 <= missing <= ADDED_COLUMNS.get(table, 0) or not is_timestamp(fields[header.index("at")]):
        return None
    return dict(zip(header, fields + [""] * missing))


def read_chunk(fd):
    return os.read(fd, SNAPSHOT_CHUNK)


def is_admin(meta):
    return meta.get("role") == "admin"


def holder_prefix(meta):
    """The landing holder prefix of a store: `.admin/` for the executive admin, else the restaurant's slug."""
    return f"{ADMIN_DIR}/" if is_admin(meta) else f"{slug(meta['restaurant'])}/"


def store_path(directory):
    """A store's path under the store root, such as app/docs. Handoff ids and relay ids start with it."""
    return f"{directory.parent.name}/{directory.name}"


HELD_LOCKS = []


class Restaurant:
    def __init__(self, directory, owner=None):
        self.dir = Path(directory)
        if not (self.dir / "restaurant.json").is_file():
            raise BrigadeError(f"{self.dir} is not a restaurant (no restaurant.json); run brigade.py open")
        if owner is not None and not re.fullmatch(r".*@\d+", owner):
            raise BrigadeError(f"--owner takes <thread>@<generation>, got {owner!r}")
        self.owner = owner
        # set --thread changes the owner, so it alone writes without matching the recorded one.
        self.unfenced = False
        self._lock_fd = None
        self._lock_depth = 0
        self._read_depth = 0
        # The warning texts this command has printed. One command makes one Restaurant for its own store.
        self.warned = set()

    def warn(self, text):
        """Print one warning line on standard error. A text this command has printed is not printed again."""
        if text not in self.warned:
            self.warned.add(text)
            print(f"brigade: warning: {text}", file=sys.stderr)

    def owner_block(self):
        """The sentence fence raises, or None when this caller may use the store."""
        meta = self.meta
        if meta.get("generation") is None:
            return None
        recorded = f"{meta.get('thread') or ''}@{meta['generation']}"
        if self.owner is None:
            return f"this store is owned by {recorded}; pass --owner <thread>@<generation> from status"
        if self.owner != recorded:
            return f"owner {self.owner} is stale; this store is owned by {recorded}"
        if not meta.get("thread"):
            return "this store has no recorded thread; it was retired, and only set --thread --expect restarts it"
        return None

    def fence(self):
        """Refuse a write from any thread but the recorded owner. Call it under the lock and keep the lock through the write."""
        block = self.owner_block()
        if block:
            raise BrigadeError(block)

    def land_owner(self):
        """The --owner words for land.py: this store's holder prefix at the caller's generation."""
        if self.owner is None:
            return []
        return ["--owner", f"{holder_prefix(self.meta)}@{self.owner.rpartition('@')[2]}"]

    @contextmanager
    def locked(self):
        """Every write in this store holds an exclusive lock on restaurant.lock.

        The lock is on a sidecar file because rewrites replace a table's inode.
        It is reentrant within one Restaurant, so a command can hold it around
        its reads and writes while each write also takes it.
        """
        if self._lock_depth == 0:
            fd = os.open(self.dir / "restaurant.lock", os.O_RDWR | os.O_CREAT, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._lock_fd = fd
            self._held = self.dir.resolve()
            HELD_LOCKS.append(self._held)
        self._lock_depth += 1
        try:
            yield
        finally:
            self._lock_depth -= 1
            if self._lock_depth == 0:
                HELD_LOCKS.remove(self._held)
                os.close(self._lock_fd)
                self._lock_fd = None

    @contextmanager
    def read_lock(self, create=True):
        """A shared lock for a read, unless this process already holds this store's lock.

        Under its own lock a command reads its own tables and takes no second lock.
        Reading another store while holding a lock raises, so the caller reads that store first.
        No process holds two stores' locks.
        create is false when a missing restaurant.lock must stay missing. A nested read takes no second lock.
        """
        if self._read_depth or self.dir.resolve() in HELD_LOCKS:
            yield
            return
        if HELD_LOCKS:
            raise BrigadeError(
                f"cannot read {self.dir} while holding {HELD_LOCKS[0]}'s lock; "
                "read other stores before taking a lock")
        lock = self.dir / "restaurant.lock"
        if not create and not lock.exists():
            self._read_depth += 1
            try:
                yield
            finally:
                self._read_depth -= 1
            return
        fd = os.open(lock, os.O_RDWR | (os.O_CREAT if create else 0), 0o644)
        self._read_depth += 1
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            yield
        finally:
            self._read_depth -= 1
            os.close(fd)

    def snapshot(self, table, offset=0):
        """The bytes of a table from offset to its end, copied while no writer can repair a tail or append.

        Every table read goes through here, rows and sync included. None when the table does not exist.
        """
        with self.read_lock():
            try:
                fd = os.open(self.dir / table, os.O_RDONLY)
            except FileNotFoundError:
                return None
            try:
                os.lseek(fd, offset, os.SEEK_SET)
                chunks = []
                while chunk := seam.read_chunk(fd):
                    chunks.append(chunk)
            finally:
                os.close(fd)
        return b"".join(chunks)

    @contextmanager
    def checked(self):
        """One command's checks and writes, run against tables that all parse.

        Nothing inside may wait on stdin, a subprocess, or a sleep. That would stop every other command on this store.
        """
        with self.locked():
            for table in TABLES:
                self.rows(table)
            yield

    @contextmanager
    def guarded(self):
        """The one gate for every change this command makes: the store lock, then the owner fence, held through the change."""
        if seam.READ_ONLY:
            raise BrigadeError("brigade: next is read-only")
        with self.locked():
            if not self.unfenced:
                self.fence()
            yield

    def write(self, relative, text):
        self.publish(self.dir / relative, text)

    def publish(self, path, text):
        """Write a file this store owns, or a handoff into a sibling's inbox."""
        with self.guarded():
            seam.write_atomic(path, text)

    def remove(self, path):
        with self.guarded():
            path.unlink()

    @property
    def meta(self):
        return json.loads((self.dir / "restaurant.json").read_text())

    def change_meta(self, drop=(), **fields):
        """Reread restaurant.json under the lock and change only these fields.

        A command that read the file before a replacement can then never write the old owner back.
        """
        with self.guarded():
            meta = self.meta
            meta.update(fields)
            for key in drop:
                meta.pop(key, None)
            self.write("restaurant.json", json.dumps(meta, indent=2) + "\n")
            return meta

    def rows(self, table):
        """The rows of a table's finished lines after its header line. A table that does not exist has none.

        A finished line ends in a newline. The bytes after the last newline are never decoded.
        A finished line that is not UTF-8, or that parse_row turns down, is refused as malformed.
        A finished header line is decoded and never parsed.
        """
        data = self.snapshot(table)
        if data is None:
            return []
        rows = []
        for number, line in enumerate(data.split(b"\n")[:-1], start=1):
            header = number == 1
            try:
                text = line.decode()
            except UnicodeDecodeError:
                text = None
            row = text if header or text is None else parse_row(table, text)
            if row is None:
                raise MalformedTable(f"{store_path(self.dir.resolve())}/{table} line {number} is malformed; fix or remove it")
            if not header:
                rows.append(row)
        return rows

    def save_rows(self, table, rows):
        header = TABLES[table]
        body = ["\t".join(header)] + ["\t".join(stored(table, key, row.get(key, "")) for key in header) for row in rows]
        self.write(table, "\n".join(body) + "\n")

    def append(self, table, row):
        data = ("\t".join(stored(table, key, row.get(key, "")) for key in TABLES[table]) + "\n").encode()
        path = self.dir / table
        with self.guarded():
            if not path.exists():
                self.save_rows(table, [])
            fd = os.open(path, os.O_RDWR | os.O_APPEND)
            try:
                end = os.fstat(fd).st_size
                if end and os.pread(fd, 1, end - 1) != b"\n":
                    # A killed append left an unfinished tail. Writing after it would join two rows.
                    end = os.pread(fd, end, 0).rfind(b"\n") + 1
                    os.ftruncate(fd, end)
                written = os.write(fd, data)
                if written != len(data):
                    os.ftruncate(fd, end)
                    raise BrigadeError(f"wrote {written} of {len(data)} bytes to {table}; nothing appended")
            finally:
                os.close(fd)

    def log(self, kind, ident, state, note=""):
        with self.guarded():
            self.append("log.tsv", {"at": seam.now(), "kind": kind, "id": ident, "state": state, "note": note})
            self.change_meta(lastActivityAt=seam.now())

    def next_id(self, table):
        numbers = [int(row["id"][1:]) for row in self.rows(table) if row.get("id", "")[1:].isdigit()]
        return f"{PREFIX[table]}{max(numbers, default=0) + 1}"

    def find(self, table, ident):
        rows = self.rows(table)
        for row in rows:
            if row["id"] == ident:
                return rows, row
        raise BrigadeError(f"no {ident} in {table}")

    def update(self, table, ident, log_kind, note=None, **fields):
        rows, row = self.find(table, ident)
        changed = {key: value for key, value in fields.items() if value is not None}
        moved = "state" in changed and changed["state"] != row.get("state")
        row.update(changed)
        self.save_rows(table, rows)
        if moved:
            self.log(log_kind, ident, changed["state"], note or row.get("summary") or row.get("question", ""))
        return row


def _read_meta(path):
    try:
        text = path.read_text()
    except OSError:
        return None, "missing"
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError:
        return None, "invalid"


def wait_for_meta(directory):
    path = directory / "restaurant.json"
    deadline = time.monotonic() + CLAIM_WAIT_SECONDS
    while True:
        meta, state = _read_meta(path)
        if state == "ok":
            return meta
        if time.monotonic() >= deadline:
            if state == "invalid":
                raise BrigadeError(f"{path} is not valid JSON")
            raise BrigadeError(f"{directory} has no restaurant.json after one second")
        time.sleep(0.01)
