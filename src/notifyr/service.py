"""Shared application operations. All methods run on a thread, inside transactions."""

import json
import re
import time
import uuid
from urllib.parse import unquote, urlsplit

from notifyr import notifications_pb2 as pb
from notifyr.config import Settings
from notifyr.contract import Error, json_message, notification, parse_message, positive_id
from notifyr.db import Database, digest


def uuid_value(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value.lower():
            raise ValueError
        return str(parsed)
    except (ValueError, AttributeError) as exc:
        raise Error(422, "invalid_uuid") from exc


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def size(value: str, limit: int, *, required: bool = False) -> None:
    try:
        if len(value.encode()) > limit or (required and not value.strip()):
            raise Error(422, "invalid_content")
    except UnicodeError as exc:
        raise Error(422, "invalid_content") from exc


def open_path(value: str) -> None:
    size(value, 1024)
    decoded = value
    for _ in range(5):
        newer = unquote(decoded)
        if newer == decoded:
            break
        decoded = newer
    try:
        parts = urlsplit(decoded)
    except ValueError as exc:
        raise Error(422, "invalid_open_path") from exc
    if (
        parts.scheme
        or parts.netloc
        or decoded.startswith("//")
        or "\\" in decoded
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded)
        or any(part in {".", ".."} for part in parts.path.split("/"))
        or "%" in decoded
    ):
        raise Error(422, "invalid_open_path")


class Service:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings

    @staticmethod
    def source(con, server: str) -> None:
        if Database.meta(con, "server_id") != server:
            raise Error(409, "server_mismatch")

    @staticmethod
    def access(con, user: str, number: int):
        row = con.execute(
            "SELECT n.*,r.responded,r.revision FROM notifications n JOIN recipients r "
            "ON n.id=r.notification WHERE n.id=? AND r.user=?",
            (number, user),
        ).fetchone()
        if row is None:
            raise Error(404, "notification_not_found")
        return row

    @staticmethod
    def state(row) -> dict:
        return json_message(
            pb.NotificationState(
                notification=notification(json.loads(row["payload"])),
                responded=bool(row["responded"]),
            )
        )

    @staticmethod
    def change(con, user: str, number: int, state: dict) -> int:
        cur = con.execute(
            "INSERT INTO changes(user,notification,state) VALUES (?,?,?)",
            (user, number, canonical(state)),
        )
        revision = cur.lastrowid
        con.execute(
            "UPDATE recipients SET revision=? WHERE notification=? AND user=?",
            (revision, number, user),
        )
        return revision

    def publish(self, app: str, payload: dict) -> dict:
        request = pb.PublishRequest()
        parse_message(payload, request)
        request.request_id = uuid_value(request.request_id)
        n = request.notification
        positive_id(str(n.id), zero=True)
        if n.id or n.from_server or n.from_app or n.HasField("created_at"):
            raise Error(422, "source_spoofing")
        if not request.HasField("notification"):
            raise Error(422, "invalid_contract")
        if len(request.recipient_user_ids) > self.settings.max_recipients:
            raise Error(422, "fanout_limit")
        users = sorted(set(request.recipient_user_ids))
        if not users or any(not user or len(user.encode()) > 128 for user in users):
            raise Error(422, "invalid_recipients")
        size(n.title, 256, required=True)
        size(n.body, 16384)
        open_path(n.open_path)
        if len(n.actions) > 8:
            raise Error(422, "action_limit")
        action_ids = set()
        for action in n.actions:
            size(action.id, 128, required=True)
            size(action.label, 128, required=True)
            if action.id in action_ids or action.kind not in (
                pb.Action.BUTTON,
                pb.Action.TEXT_REPLY,
            ):
                raise Error(422, "invalid_actions")
            action_ids.add(action.id)
        expires = n.expires_at.ToNanoseconds() if n.HasField("expires_at") else None
        request.ClearField("recipient_user_ids")
        request.recipient_user_ids.extend(users)
        fingerprint = digest(canonical(json_message(request)))
        with self.db.transaction() as con:
            for user in users:
                if not con.execute(
                    "SELECT 1 FROM grants WHERE app=? AND user=?", (app, user)
                ).fetchone():
                    raise Error(403, "recipient_not_authorized")
            old = con.execute(
                "SELECT fingerprint,payload FROM notifications WHERE app=? AND request_id=?",
                (app, request.request_id),
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise Error(409, "idempotency_conflict")
                return json.loads(old["payload"])
            if expires is not None and expires <= time.time_ns():
                raise Error(422, "invalid_expiry")
            number = int(self.db.meta(con, "next_id"))
            if number > 2**63 - 1:
                raise Error(503, "id_space_exhausted")
            con.execute("UPDATE meta SET value=? WHERE key='next_id'", (str(number + 1),))
            n.id = number
            n.created_at.FromNanoseconds(time.time_ns())
            n.from_server = self.db.meta(con, "server_id")
            n.from_app = app
            stored = json_message(n)
            con.execute(
                "INSERT INTO notifications VALUES (?,?,?,?,?,?)",
                (
                    number,
                    app,
                    request.request_id,
                    fingerprint,
                    canonical(stored),
                    str(expires) if expires is not None else None,
                ),
            )
            for user in users:
                con.execute(
                    "INSERT INTO recipients(notification,user) VALUES (?,?)", (number, user)
                )
                self.change(con, user, number, json_message(pb.NotificationState(notification=n)))
                self.db.enqueue(con, user, number, "notification")
            return stored

    def get(self, user: str, number: str, server: str) -> dict:
        with self.db.transaction(write=False) as con:
            self.source(con, server)
            return self.state(self.access(con, user, positive_id(number)))

    def respond(self, user: str, payload: dict) -> dict:
        request = pb.RespondRequest()
        parse_message(payload, request)
        request.response_id = uuid_value(request.response_id)
        number = positive_id(str(request.notification_id))
        if "text" in payload and payload["text"] is None:
            raise Error(422, "invalid_text")
        fingerprint = digest(canonical(json_message(request)))
        with self.db.transaction() as con:
            self.source(con, request.from_server)
            row = self.access(con, user, number)
            old = con.execute(
                "SELECT fingerprint,receipt FROM responses WHERE user=? AND response_id=?",
                (user, request.response_id),
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise Error(409, "idempotency_conflict")
                return json.loads(old["receipt"])
            # ponytail: one response per recipient per notification. Publish a new
            # notification for follow-ups; shared first-response-wins needs a new policy field.
            if row["responded"]:
                raise Error(409, "already_responded")
            n = notification(json.loads(row["payload"]))
            action = next((a for a in n.actions if a.id == request.action_id), None)
            if action is None:
                raise Error(422, "unknown_action")
            has_text = request.HasField("text")
            if action.kind == pb.Action.BUTTON and has_text:
                raise Error(422, "unexpected_text")
            if action.kind == pb.Action.TEXT_REPLY:
                if not has_text:
                    raise Error(422, "text_required")
                size(request.text, 4096, required=True)
            if row["expires_ns"] is not None and time.time_ns() >= int(row["expires_ns"]):
                raise Error(410, "notification_expired")
            receipt = pb.ResponseReceipt(response_id=request.response_id)
            receipt.accepted_at.FromNanoseconds(time.time_ns())
            result = json_message(receipt)
            returned = json_message(request) | {
                "responder_user_id": user,
                "from_app": row["app"],
                "accepted_at": result["accepted_at"],
            }
            con.execute(
                "INSERT INTO responses(user,notification,response_id,fingerprint,receipt,"
                "payload,app) VALUES (?,?,?,?,?,?,?)",
                (
                    user,
                    number,
                    request.response_id,
                    fingerprint,
                    canonical(result),
                    canonical(returned),
                    row["app"],
                ),
            )
            con.execute(
                "UPDATE recipients SET responded=1 WHERE notification=? AND user=?", (number, user)
            )
            self.change(
                con,
                user,
                number,
                json_message(pb.NotificationState(notification=n, responded=True)),
            )
            self.db.enqueue(con, user, number, "state_update")
            return result

    def page(self, principal: str, cursor: str, limit: int, *, responses: bool = False) -> dict:
        if not re.fullmatch(r"0|[1-9][0-9]{0,18}", cursor) or int(cursor) > 2**63 - 1:
            raise Error(422, "invalid_cursor")
        if not 1 <= limit <= 100:
            raise Error(422, "invalid_limit")
        with self.db.transaction(write=False) as con:
            # Append-only snapshots and a consistent read transaction: answering an older
            # notification creates a new revision, never mutates an already paged snapshot.
            if responses:
                rows = con.execute(
                    "SELECT revision,payload FROM responses WHERE app=? "
                    "AND revision>? ORDER BY revision LIMIT ?",
                    (principal, int(cursor), limit + 1),
                ).fetchall()
                items = [
                    json.loads(row["payload"]) | {"revision": str(row["revision"])}
                    for row in rows[:limit]
                ]
            else:
                rows = con.execute(
                    "SELECT revision,state FROM changes WHERE user=? "
                    "AND revision>? ORDER BY revision LIMIT ?",
                    (principal, int(cursor), limit + 1),
                ).fetchall()
                items = [
                    {"revision": str(row["revision"]), "state": json.loads(row["state"])}
                    for row in rows[:limit]
                ]
            return {
                "items": items,
                "cursor": str(rows[min(len(rows), limit) - 1]["revision"]) if rows else cursor,
                "has_more": len(rows) > limit,
            }
