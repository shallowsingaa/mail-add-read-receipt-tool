"""Durable link storage and outbox. Each operation owns its SQLite connection."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
import uuid


class RateLimited(Exception):
    pass


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS links (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL, note TEXT NOT NULL,
                    secret_hash TEXT NOT NULL, created REAL NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    link_id TEXT NOT NULL REFERENCES links(id) ON DELETE CASCADE,
                    created REAL NOT NULL, details TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL, deadline REAL NOT NULL,
                    lease_until REAL, sent_at REAL, last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS events_due ON events(status,next_attempt);
                CREATE INDEX IF NOT EXISTS events_link ON events(link_id,created);
                CREATE TABLE IF NOT EXISTS creations (ip TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS creations_ip ON creations(ip,created);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, email, note, ip, limit, window):
        now = time.time()
        link_id, secret = secrets.token_hex(24), secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM creations WHERE created < ?", (now - window,))
            count = db.execute("SELECT count(*) FROM creations WHERE ip=?", (ip,)).fetchone()[0]
            if count >= limit:
                raise RateLimited()
            db.execute("INSERT INTO creations VALUES (?,?)", (ip, now))
            db.execute("INSERT INTO links(id,email,note,secret_hash,created) VALUES (?,?,?,?,?)",
                       (link_id, email, note, hashlib.sha256(secret.encode()).hexdigest(), now))
        return link_id, secret

    def authorized(self, link_id, secret):
        with self.connect() as db:
            row = db.execute("SELECT * FROM links WHERE id=?", (link_id,)).fetchone()
        if row and secrets.compare_digest(row["secret_hash"], hashlib.sha256(secret.encode()).hexdigest()):
            return dict(row)
        return None

    def record(self, link_id, details, retry_hours):
        now = time.time()
        event_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT active FROM links WHERE id=?", (link_id,)).fetchone()
            if not row or not row["active"]:
                return None
            db.execute("INSERT INTO events(id,link_id,created,details,next_attempt,deadline) VALUES (?,?,?,?,?,?)",
                       (event_id, link_id, now, json.dumps(details, ensure_ascii=False), now,
                        now + retry_hours * 3600))
        return event_id

    def stop(self, link_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE links SET active=0 WHERE id=?", (link_id,))
            db.execute("UPDATE events SET status='cancelled',lease_until=NULL WHERE link_id=? AND status IN ('pending','sending','failed')", (link_id,))

    def delete(self, link_id):
        with self.connect() as db:
            db.execute("DELETE FROM links WHERE id=?", (link_id,))

    def events(self, link_id, limit=100):
        with self.connect() as db:
            rows = db.execute("SELECT id,created,status,attempts,sent_at,last_error FROM events WHERE link_id=? ORDER BY created DESC LIMIT ?", (link_id, limit)).fetchall()
            counts = db.execute("SELECT status,count(*) AS n FROM events WHERE link_id=? GROUP BY status", (link_id,)).fetchall()
        return {"counts": {r["status"]: r["n"] for r in counts}, "events": [dict(r) for r in rows]}

    def retry(self, link_id, hours):
        now = time.time()
        with self.connect() as db:
            return db.execute("UPDATE events SET status='pending',attempts=0,next_attempt=?,deadline=?,last_error=NULL WHERE link_id=? AND status='failed' AND EXISTS(SELECT 1 FROM links WHERE id=? AND active=1)", (now, now + hours * 3600, link_id, link_id)).rowcount

    def maintain(self, retention_days, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("UPDATE events SET status='pending',lease_until=NULL WHERE status='sending' AND lease_until < ?", (now,))
            db.execute("UPDATE events SET status='failed',last_error='Retry deadline exceeded' WHERE status='pending' AND deadline <= ?", (now,))
            db.execute("DELETE FROM events WHERE status IN ('sent','cancelled') AND created < ?", (now - retention_days * 86400,))

    def claim(self, lease_seconds):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT e.*,l.email,l.note FROM events e JOIN links l ON e.link_id=l.id WHERE e.status='pending' AND l.active=1 AND e.next_attempt<=? AND e.deadline>? ORDER BY e.next_attempt,e.created LIMIT 1", (now, now)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE events SET status='sending',attempts=attempts+1,lease_until=? WHERE id=?", (now + lease_seconds, row["id"]))
        event = dict(row)
        event["attempts"] += 1
        return event

    def finish(self, event, error=None):
        now = time.time()
        with self.connect() as db:
            if error is None:
                db.execute("UPDATE events SET status='sent',sent_at=?,lease_until=NULL,last_error=NULL WHERE id=? AND status='sending'", (now, event["id"]))
            else:
                delay = min(3600, 30 * 2 ** min(event["attempts"] - 1, 7))
                status = "failed" if now >= event["deadline"] else "pending"
                db.execute("UPDATE events SET status=?,next_attempt=?,lease_until=NULL,last_error=? WHERE id=? AND status='sending'", (status, min(now + delay, event["deadline"]), error, event["id"]))

