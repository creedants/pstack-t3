"""Shared SQLite task market with expiring, fenced claims."""
from contextlib import contextmanager
from pathlib import Path
import json
import math
import os
import sqlite3
import time
import uuid


SCHEMA = """
CREATE TABLE IF NOT EXISTS peers (
 id TEXT PRIMARY KEY, thread_id TEXT UNIQUE NOT NULL,
 workspace TEXT UNIQUE NOT NULL, capabilities TEXT NOT NULL,
 updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, scope TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'ready', priority INTEGER NOT NULL DEFAULT 0,
 deadline REAL, max_attempts INTEGER NOT NULL DEFAULT 3,
 attempts INTEGER NOT NULL DEFAULT 0, owner TEXT REFERENCES peers(id),
 token TEXT, expires REAL, result TEXT, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dependencies (
 task_id TEXT REFERENCES tasks(id), dependency_id TEXT REFERENCES tasks(id),
 PRIMARY KEY(task_id, dependency_id)
);
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
 peer_id TEXT, task_id TEXT, data TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
 id TEXT PRIMARY KEY, sender TEXT REFERENCES peers(id),
 recipient TEXT REFERENCES peers(id), body TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', transport_id TEXT,
 created REAL NOT NULL
);
"""


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')


def _lease(value):
    _number(value, 'lease_seconds')
    if not 0 < value <= 3600:
        raise ValueError('lease_seconds must be between 0 and 3600')


def _json(value):
    return json.dumps(value, allow_nan=False)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Board:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except sqlite3.IntegrityError as error:
            db.rollback()
            raise ValueError(str(error)) from error
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def event(db, kind, peer_id=None, task_id=None, data=None):
        db.execute('INSERT INTO events(kind,peer_id,task_id,data,created) VALUES(?,?,?,?,?)',
                   (kind, peer_id, task_id, _json({} if data is None else data), time.time()))

    @staticmethod
    def require_running(db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_config'").fetchone():
            return {}
        config = {row['key']: json.loads(row['value']) for row in db.execute('SELECT * FROM run_config')}
        if ('goal_state' in config or 'project_database' in config) and config.get('goal_state') != 'running':
            raise ValueError('Mesh goal is not running')
        return config

    def register(self, id, thread_id, workspace, capabilities=None):
        _text(id, 'id')
        _text(thread_id, 'thread_id')
        if not isinstance(workspace, (str, os.PathLike)) or not str(workspace).strip():
            raise ValueError('workspace must be a nonempty path')
        workspace = str(Path(workspace).resolve())
        with self.transaction() as db:
            old = db.execute('SELECT * FROM peers WHERE id=?', (id,)).fetchone()
            if old and (old['thread_id'] != thread_id or os.path.normcase(old['workspace']) != os.path.normcase(workspace)):
                raise ValueError('Peer identity already belongs to another thread/workspace')
            for peer in db.execute('SELECT id, workspace FROM peers WHERE id!=?', (id,)):
                if os.path.normcase(peer['workspace']) == os.path.normcase(workspace):
                    raise ValueError('Workspace already belongs to another peer')
            db.execute('INSERT INTO peers VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET capabilities=excluded.capabilities, updated=excluded.updated',
                       (id, thread_id, workspace, _json([] if capabilities is None else capabilities), time.time()))
            self.event(db, 'registered', id)
        return {'id': id, 'thread_id': thread_id, 'workspace': workspace}

    def peers(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM peers ORDER BY id')]

    def add(self, id, title, scope, priority=0, dependencies=None, deadline=None, max_attempts=3):
        _text(id, 'id')
        _text(title, 'title')
        _text(scope, 'scope')
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError('max_attempts must be a positive integer')
        if type(priority) is not int:
            raise ValueError('priority must be an integer')
        if deadline is not None:
            _number(deadline, 'deadline')
        if dependencies is not None and not isinstance(dependencies, (list, tuple)):
            raise ValueError('dependencies must be a list or tuple')
        for dependency in dependencies or []:
            _text(dependency, 'dependency')
        with self.transaction() as db:
            if db.execute('SELECT 1 FROM tasks WHERE id=?', (id,)).fetchone():
                raise ValueError('Task already exists')
            db.execute('INSERT INTO tasks(id,title,scope,priority,deadline,max_attempts,created) VALUES(?,?,?,?,?,?,?)',
                       (id, title, scope, priority, deadline, max_attempts, time.time()))
            for dependency in dependencies or []:
                if dependency == id:
                    raise ValueError('Task cannot depend on itself')
                db.execute('INSERT INTO dependencies VALUES(?,?)', (id, dependency))
            self.event(db, 'task_added', task_id=id)
        return {'id': id}

    def _recover(self, db, now):
        rows = db.execute("""SELECT * FROM tasks WHERE status IN ('ready','claimed') AND
            ((deadline IS NOT NULL AND deadline<=?) OR (status='claimed' AND expires<=?))""", (now, now)).fetchall()
        for row in rows:
            deadline_expired = row['deadline'] is not None and row['deadline'] <= now
            status = 'failed' if deadline_expired or row['attempts'] >= row['max_attempts'] else 'ready'
            db.execute('UPDATE tasks SET status=?,owner=NULL,token=NULL,expires=NULL WHERE id=?', (status, row['id']))
            self.event(db, 'deadline_expired' if deadline_expired else 'lease_expired', row['owner'], row['id'], {'status': status})

    def claim(self, peer_id, lease_seconds=600, task_id=None):
        _lease(lease_seconds)
        with self.transaction() as db:
            self.require_running(db)
            now = time.time()
            if not db.execute('SELECT 1 FROM peers WHERE id=?', (peer_id,)).fetchone():
                raise ValueError('Register before claiming')
            self._recover(db, now)
            if db.execute("SELECT 1 FROM tasks WHERE owner=? AND status='claimed'", (peer_id,)).fetchone():
                raise ValueError('Peer already holds a claim')
            row = db.execute("""SELECT * FROM tasks t WHERE status='ready'
                AND (? IS NULL OR t.id=?) AND NOT EXISTS (
                  SELECT 1 FROM dependencies d LEFT JOIN tasks p ON p.id=d.dependency_id
                  WHERE d.task_id=t.id AND (p.status IS NOT 'done'
                    OR json_extract(p.result,'$.status') IS NOT 'PASS'))
                ORDER BY priority DESC,created,id LIMIT 1""", (task_id, task_id)).fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            expires = min(now + lease_seconds, row['deadline']) if row['deadline'] is not None else now + lease_seconds
            db.execute("UPDATE tasks SET status='claimed',owner=?,token=?,expires=?,attempts=attempts+1 WHERE id=?",
                       (peer_id, token, expires, row['id']))
            self.event(db, 'claimed', peer_id, row['id'], {'token': token, 'expires': expires})
            return dict(db.execute('SELECT * FROM tasks WHERE id=?', (row['id'],)).fetchone())

    @staticmethod
    def _owned(db, task_id, peer_id, token):
        Board.require_running(db)
        row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        now = time.time()
        if not row or row['status'] != 'claimed' or row['owner'] != peer_id or row['token'] != token or row['expires'] <= now or (row['deadline'] is not None and row['deadline'] <= now):
            raise ValueError('Claim expired, superseded, or owned by another peer')
        return row

    def heartbeat(self, task_id, peer_id, token, lease_seconds=600):
        _lease(lease_seconds)
        with self.transaction() as db:
            row = self._owned(db, task_id, peer_id, token)
            expires = time.time() + lease_seconds
            if row['deadline'] is not None:
                expires = min(expires, row['deadline'])
            db.execute('UPDATE tasks SET expires=? WHERE id=?', (expires, task_id))
            self.event(db, 'heartbeat', peer_id, task_id, {'expires': expires})
        return {'expires': expires}

    def finish(self, task_id, peer_id, token, result):
        with self.transaction() as db:
            self._owned(db, task_id, peer_id, token)
            db.execute("UPDATE tasks SET status='done',result=?,token=NULL,expires=NULL WHERE id=?", (_json(result), task_id))
            self.event(db, 'finished', peer_id, task_id, result)
        return {'id': task_id, 'status': 'done'}

    def recover(self):
        with self.transaction() as db:
            self.require_running(db)
            self._recover(db, time.time())
        return self.tasks()

    def tasks(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM tasks ORDER BY priority DESC,created,id')]

    def record(self, kind, peer_id=None, task_id=None, data=None):
        with self.transaction() as db:
            self.event(db, kind, peer_id, task_id, data)
        return {'kind': kind}

    def events(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM events ORDER BY seq')]

    def message(self, id, sender, recipient, body):
        _text(id, 'id')
        if sender == recipient or not isinstance(body, str) or not 0 < len(body) <= 8000:
            raise ValueError('Send 1-8000 characters to another peer')
        with self.transaction() as db:
            old = db.execute('SELECT * FROM messages WHERE id=?', (id,)).fetchone()
            if old:
                if (old['sender'], old['recipient'], old['body']) != (sender, recipient, body):
                    raise ValueError('Message ID reused for different content')
                return dict(old)
            if db.execute('SELECT COUNT(*) FROM messages WHERE sender=?', (sender,)).fetchone()[0] >= 20:
                raise ValueError('Peer message budget of 20 reached for this run')
            db.execute('INSERT INTO messages(id,sender,recipient,body,created) VALUES(?,?,?,?,?)', (id, sender, recipient, body, time.time()))
            self.event(db, 'message_pending', sender, data={'id': id, 'recipient': recipient})
            return dict(db.execute('SELECT * FROM messages WHERE id=?', (id,)).fetchone())

    def delivered(self, id, transport_id):
        _text(transport_id, 'transport_id')
        with self.transaction() as db:
            row = db.execute('SELECT * FROM messages WHERE id=?', (id,)).fetchone()
            if not row:
                raise ValueError('Unknown message')
            if row['transport_id'] and row['transport_id'] != transport_id:
                raise ValueError('Message already has a different transport ID')
            db.execute("UPDATE messages SET state='delivered',transport_id=? WHERE id=?", (transport_id, id))
            if row['state'] != 'delivered':
                self.event(db, 'message_delivered', row['sender'], data={'id': id, 'recipient': row['recipient'], 'transport_id': transport_id})
        return {'id': id, 'state': 'delivered'}

    def messages(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM messages ORDER BY created,id')]
