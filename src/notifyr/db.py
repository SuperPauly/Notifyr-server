"""SQLite transactions, stable identity, principal provisioning and job insertion."""

import base64
import hashlib
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid

from notifyr.contract import Error

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS apps (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS grants (
 app TEXT REFERENCES apps(id), user TEXT REFERENCES users(id), PRIMARY KEY(app,user));
CREATE TABLE IF NOT EXISTS tokens (
 hash TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('recipient','publisher')),
 user TEXT REFERENCES users(id), app TEXT REFERENCES apps(id), revoked INTEGER NOT NULL DEFAULT 0,
 CHECK((scope='recipient' AND user IS NOT NULL AND app IS NULL) OR
       (scope='publisher' AND app IS NOT NULL AND user IS NULL)));
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY CHECK(id>0), app TEXT NOT NULL REFERENCES apps(id),
 request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
 expires_ns TEXT, UNIQUE(app,request_id));
CREATE TABLE IF NOT EXISTS recipients (
 notification INTEGER REFERENCES notifications(id), user TEXT REFERENCES users(id),
 responded INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(notification,user));
CREATE TABLE IF NOT EXISTS changes (
 revision INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL REFERENCES users(id),
 notification INTEGER NOT NULL REFERENCES notifications(id), state TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS changes_user ON changes(user,revision);
CREATE TABLE IF NOT EXISTS responses (
 revision INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL REFERENCES users(id),
 notification INTEGER NOT NULL REFERENCES notifications(id),
 response_id TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL, payload TEXT NOT NULL,
 app TEXT NOT NULL REFERENCES apps(id), UNIQUE(user,response_id), UNIQUE(user,notification));
CREATE INDEX IF NOT EXISTS responses_app ON responses(app,revision);
CREATE TABLE IF NOT EXISTS subscriptions (
 installation TEXT PRIMARY KEY, user TEXT NOT NULL REFERENCES users(id),
 version INTEGER NOT NULL, type TEXT NOT NULL, endpoint TEXT UNIQUE, p256dh TEXT, auth TEXT,
 status TEXT NOT NULL, challenge_hash TEXT, challenge_expires REAL);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, installation TEXT NOT NULL REFERENCES subscriptions,
 version INTEGER NOT NULL, notification INTEGER REFERENCES notifications(id),
 user TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL, payload TEXT,
 status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 due REAL NOT NULL, lease_until REAL, lease_token TEXT, diagnostic TEXT);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status,due,lease_until);
"""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


class Database:
    # ponytail: SQLite and a single Uvicorn process bound write throughput. Move
    # transactions/leases to PostgreSQL before scaling to concurrent server writers.
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA synchronous=FULL")
        return con

    @contextmanager
    def transaction(self, *, write: bool = True):
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        existing = self.path.exists() and self.path.stat().st_size > 0
        # Use exclusive creation with owner-only permissions, before SQLite opens it.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        con = self.connect()
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
        finally:
            con.close()
        with self.transaction() as con:
            count = con.execute("SELECT count(*) FROM meta").fetchone()[0]
            if not count:
                if existing:
                    raise RuntimeError("Missing identity metadata; restore a consistent backup")
                vapid = Vapid()
                vapid.generate_keys()
                private = b64(
                    vapid.private_key.private_bytes(
                        serialization.Encoding.DER,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    )
                )
                public = b64(
                    vapid.public_key.public_bytes(
                        serialization.Encoding.X962,
                        serialization.PublicFormat.UncompressedPoint,
                    )
                )
                con.executemany(
                    "INSERT INTO meta VALUES (?,?)",
                    [
                        ("server_id", str(uuid.uuid4())),
                        ("next_id", "1"),
                        ("vapid_private", private),
                        ("vapid_public", public),
                        ("schema_version", "1"),
                    ],
                )
            else:
                keys = {row[0] for row in con.execute("SELECT key FROM meta")}
                if keys != {
                    "server_id",
                    "next_id",
                    "vapid_private",
                    "vapid_public",
                    "schema_version",
                }:
                    raise RuntimeError("Incomplete identity metadata; restore a consistent backup")
                try:
                    server_id = self.meta(con, "server_id")
                    if str(uuid.UUID(server_id)) != server_id:
                        raise ValueError
                    if self.meta(con, "schema_version") != "1":
                        raise ValueError
                    next_value = self.meta(con, "next_id")
                    next_id = int(next_value)
                    maximum = con.execute(
                        "SELECT coalesce(max(id),0) FROM notifications"
                    ).fetchone()[0]
                    if str(next_id) != next_value or not maximum < next_id <= 2**63:
                        raise ValueError
                    vapid = Vapid.from_der(self.meta(con, "vapid_private").encode())
                    public = b64(
                        vapid.public_key.public_bytes(
                            serialization.Encoding.X962,
                            serialization.PublicFormat.UncompressedPoint,
                        )
                    )
                    if not secrets.compare_digest(public, self.meta(con, "vapid_public")):
                        raise ValueError
                except (ValueError, TypeError) as exc:
                    raise RuntimeError(
                        "Invalid identity metadata; restore a consistent backup"
                    ) from exc

    @staticmethod
    def meta(con: sqlite3.Connection, key: str) -> str:
        return con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0]

    def info(self) -> dict:
        with self.transaction(write=False) as con:
            return {
                "server_id": self.meta(con, "server_id"),
                "vapid_public_key": self.meta(con, "vapid_public"),
                "contract": "notifications.v2",
            }

    def provision(self, scope: str, principal: str, recipients: list[str]) -> str:
        if not principal or len(principal.encode()) > 128:
            raise ValueError("Principal must contain 1..128 UTF-8 bytes")
        token = secrets.token_urlsafe(32)
        with self.transaction() as con:
            if scope == "recipient":
                con.execute("INSERT OR IGNORE INTO users VALUES (?)", (principal,))
                con.execute(
                    "INSERT INTO tokens(hash,scope,user) VALUES (?,?,?)",
                    (digest(token), scope, principal),
                )
            elif scope == "publisher":
                con.execute("INSERT OR IGNORE INTO apps VALUES (?)", (principal,))
                for user in recipients:
                    con.execute("INSERT OR IGNORE INTO grants VALUES (?,?)", (principal, user))
                con.execute(
                    "INSERT INTO tokens(hash,scope,app) VALUES (?,?,?)",
                    (digest(token), scope, principal),
                )
            else:
                raise ValueError("Unknown scope")
        return token

    def authenticate(self, token: str) -> dict:
        with self.transaction(write=False) as con:
            row = con.execute(
                "SELECT scope,user,app FROM tokens WHERE hash=? AND revoked=0", (digest(token),)
            ).fetchone()
            if row is None:
                raise Error(401, "unauthorized")
            return dict(row)

    @staticmethod
    def enqueue(con, user: str, notification: int, kind: str) -> None:
        subs = con.execute(
            "SELECT installation,version FROM subscriptions WHERE user=? "
            "AND status='active' AND (?='notification' OR type='unifiedpush')",
            (user, kind),
        ).fetchall()
        for sub in subs:
            con.execute(
                "INSERT INTO jobs(installation,version,notification,user,kind,due) "
                "VALUES (?,?,?,?,?,?)",
                (sub[0], sub[1], notification, user, kind, time.time()),
            )
