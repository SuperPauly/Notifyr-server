import base64
import json
import socket
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import Mock

import dns.resolver
import http_ece
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from test_server import auth, publication, publish, response, uid

from notifyr.api import create_app
from notifyr.config import Settings
from notifyr.contract import Error
from notifyr.db import Database, b64
from notifyr.push import (
    PinnedConnection,
    PinnedSession,
    Policy,
    Transport,
    Worker,
    resolve_addresses,
    retry_delay,
)


class LocalTransport:
    def __init__(self):
        self.sent = []
        self.results = []

    def send(self, sub, payload, ttl):
        self.sent.append((dict(sub), json.loads(payload), ttl))
        result = self.results.pop(0) if self.results else (201, "")
        if isinstance(result, Exception):
            raise result
        return result


def keypair():
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return private, b64(public), b64(b"a" * 16)


@pytest.fixture
def push_server(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "notifyr.push.resolve_addresses",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))
        ],
    )
    settings = Settings(
        database=tmp_path / "db", worker_enabled=False, push_origins=("https://push.example",)
    )
    transport = LocalTransport()
    app = create_app(settings, transport=transport)
    with TestClient(app) as client:
        db = app.state.db
        users = {user: db.provision("recipient", user, []) for user in ("alice", "bob")}
        publisher = db.provision("publisher", "app", ["alice", "bob"])
        yield client, app, users, publisher, transport, settings


def register(server, user="alice", delivery_type="unifiedpush", installation=None, endpoint=None):
    client, app, users, _, _, _ = server
    _, public, secret = keypair()
    installation = installation or uid()
    payload = {
        "delivery_type": delivery_type,
        "endpoint": endpoint or "https://push.example/" + uid(),
        "p256dh": public,
        "auth": secret,
    }
    result = client.put(
        "/v1/subscriptions/" + installation, json=payload, headers=auth(users[user])
    )
    assert result.status_code == 200, result.text
    with app.state.db.transaction(write=False) as con:
        row = con.execute(
            "SELECT payload FROM jobs WHERE installation=? ORDER BY id DESC LIMIT 1",
            (installation,),
        ).fetchone()
        challenge = json.loads(row[0])["challenge"]
    return installation, result.json()["version"], challenge, payload


def confirm(server, registration, user="alice"):
    client, _, users, _, _, _ = server
    installation, version, challenge, _ = registration
    return client.post(
        f"/v1/subscriptions/{installation}/confirm",
        json={"version": version, "challenge": challenge},
        headers=auth(users[user]),
    )


def job_rows(app):
    with app.state.db.transaction(write=False) as con:
        return [dict(row) for row in con.execute("SELECT * FROM jobs ORDER BY id")]


def due_now(app):
    with app.state.db.transaction() as con:
        con.execute("UPDATE jobs SET due=0 WHERE status='pending'")


def test_ownership_pending_rotation_and_single_use_challenge(push_server):
    client, app, users, publisher, transport, _ = push_server
    reg = register(push_server)
    installation, version, challenge, payload = reg
    n = publish(client, publisher)
    assert all(row["notification"] is None for row in job_rows(app))
    assert app.state.worker.run_one()
    encrypted_challenge = transport.sent[0][1]
    assert encrypted_challenge["challenge"] == challenge
    assert encrypted_challenge["visible_notification"]["title"]
    assert client.put(
        "/v1/subscriptions/" + installation, json=payload, headers=auth(users["bob"])
    ).json() == {"error": "subscription_not_owned"}
    assert confirm(push_server, reg, user="bob").status_code == 404
    bad = client.post(
        f"/v1/subscriptions/{installation}/confirm",
        json={"version": version, "challenge": "incorrect-challenge-value"},
        headers=auth(users["alice"]),
    )
    assert bad.json() == {"error": "invalid_challenge"}
    assert confirm(push_server, reg).json()["status"] == "active"
    assert confirm(push_server, reg).json() == {"error": "challenge_not_current"}
    publish(client, publisher)
    old_job = app.state.worker.claim()
    rotated = register(push_server, installation=installation)
    assert rotated[1] == 2
    assert app.state.worker.prepare(old_job) is None
    assert confirm(push_server, reg).status_code == 409
    assert confirm(push_server, rotated).status_code == 200
    assert (
        client.delete("/v1/subscriptions/" + installation, headers=auth(users["bob"])).status_code
        == 404
    )
    assert (
        client.delete("/v1/subscriptions/" + installation, headers=auth(users["alice"])).status_code
        == 204
    )
    assert (
        client.put(
            "/v1/subscriptions/" + installation, json=payload, headers=auth(users["bob"])
        ).status_code
        == 403
    )
    with app.state.db.transaction(write=False) as con:
        sub = con.execute(
            "SELECT * FROM subscriptions WHERE installation=?", (installation,)
        ).fetchone()
        assert sub["endpoint"] is None and sub["auth"] is None
    assert n["id"] == "1"


def test_duplicate_endpoint_key_validation_and_expired_challenge(push_server, monkeypatch):
    client, app, users, _, _, _ = push_server
    reg = register(push_server)
    payload = reg[3]
    assert client.put(
        "/v1/subscriptions/" + uid(), json=payload, headers=auth(users["bob"])
    ).json() == {"error": "endpoint_already_registered"}
    for field, value in [
        ("auth", "bad!"),
        ("auth", b64(b"short")),
        ("p256dh", b64(b"x" * 65)),
        ("endpoint", "http://push.example"),
    ]:
        invalid = payload | {field: value}
        assert (
            client.put(
                "/v1/subscriptions/" + uid(), json=invalid, headers=auth(users["alice"])
            ).status_code
            == 422
        )
    with app.state.db.transaction() as con:
        con.execute("UPDATE subscriptions SET challenge_expires=0")
    assert confirm(push_server, reg).json() == {"error": "challenge_expired"}
    assert app.state.worker.run_one()
    assert job_rows(app)[0]["status"] == "discarded"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://push.example/a",
        "https://u:p@push.example/a",
        "https://push.example/a#b",
        "https://evil.example/a",
        "https://push.example\\@evil.example/a",
    ],
)
def test_endpoint_policy(endpoint):
    policy = Policy(Settings(push_origins=("https://push.example",)))
    with pytest.raises(Error):
        policy.validate(endpoint)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
        "100.64.0.1",
    ],
)
def test_actual_connection_blocks_non_global(monkeypatch, address):
    policy = Policy(Settings(push_origins=("https://push.example",)))
    monkeypatch.setattr(
        "notifyr.push.resolve_addresses",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443))],
    )
    make_socket = Mock()
    monkeypatch.setattr(socket, "socket", make_socket)
    connection = PinnedConnection("https://push.example/a", policy, 1)
    with pytest.raises(Error, match="push_destination_blocked"):
        connection.connect()
    make_socket.assert_not_called()


def test_private_exception_and_mixed_dns_and_rebinding(monkeypatch):
    settings = Settings(
        push_origins=("https://push.example",), private_push_origins=("https://private.example",)
    )
    policy = Policy(settings)
    monkeypatch.setattr(
        "notifyr.push.resolve_addresses",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.2", 443))],
    )
    assert policy.addresses("https://private.example/path")[0][4][0] == "10.0.0.2"
    with pytest.raises(Error):
        policy.addresses("https://push.example/path")
    monkeypatch.setattr(
        "notifyr.push.resolve_addresses",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 443)),
        ],
    )
    with pytest.raises(Error):
        policy.addresses("https://push.example/path")
    resolver = Mock(
        side_effect=[
            [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443))],
        ]
    )
    monkeypatch.setattr("notifyr.push.resolve_addresses", resolver)
    policy.addresses("https://push.example/path")
    with pytest.raises(Error):
        PinnedConnection("https://push.example/path", policy, 1).connect()


def test_connection_pins_ip_and_verifies_original_hostname(monkeypatch):
    policy = Policy(Settings(push_origins=("https://push.example",)))
    resolver = Mock(
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))
        ]
    )
    monkeypatch.setattr("notifyr.push.resolve_addresses", resolver)
    sock = Mock()
    sock.getpeername.return_value = ("8.8.8.8", 443)
    monkeypatch.setattr(socket, "socket", Mock(return_value=sock))
    connection = PinnedConnection("https://push.example/path", policy, 1)
    context = Mock()
    connection.tls_context = context
    connection.connect()
    sock.connect.assert_called_once_with(("8.8.8.8", 443))
    context.wrap_socket.assert_called_once_with(sock, server_hostname="push.example")
    assert resolver.call_count == 1


def test_post_does_not_follow_redirects_or_read_provider_body(monkeypatch):
    connection = Mock()
    connection.getresponse.return_value.status = 302
    connection.getresponse.return_value.getheader.return_value = ""
    factory = Mock(return_value=connection)
    monkeypatch.setattr("notifyr.push.PinnedConnection", factory)
    policy = Policy(Settings(push_origins=("https://push.example",)))
    session = PinnedSession(policy)
    result = session.post("https://push.example/path?x=1", data=b"encrypted", timeout=1)
    assert result.status_code == 302 and result.content == b""
    assert not session.trust_env
    assert factory.call_count == 1
    connection.request.assert_called_once_with("POST", "/path?x=1", body=b"encrypted", headers={})
    connection.getresponse.return_value.read.assert_not_called()


def test_real_rfc8291_encryption_vapid_and_persistent_identity(tmp_path, monkeypatch):
    db = Database(tmp_path / "db")
    db.initialize()
    original = db.info()
    db.initialize()
    assert db.info() == original
    private, public, secret = keypair()
    captured = {}

    def post(self, url, data=None, **kwargs):
        captured.update(endpoint=url, body=data, headers=kwargs["headers"])
        result = requests.Response()
        result.status_code, result._content = 201, b""
        return result

    monkeypatch.setattr(PinnedSession, "post", post)
    settings = Settings(database=db.path, push_origins=("https://push.example",))
    transport = Transport(db, settings, Policy(settings))
    sub = {"endpoint": "https://push.example/secret", "p256dh": public, "auth": secret}
    assert transport.send(sub, '{"challenge":"hello"}', 60) == (201, "")
    headers = captured["headers"]
    assert headers["content-encoding"] == "aes128gcm" and headers["ttl"] == "60"
    assert headers["authorization"].startswith("vapid ")
    decoded = http_ece.decrypt(
        captured["body"],
        private_key=private,
        auth_secret=base64.urlsafe_b64decode(secret + "=="),
        version="aes128gcm",
    )
    assert decoded == b'{"challenge":"hello"}'
    assert captured["body"] != decoded


def test_worker_retries_recovery_invalid_subscriptions_and_diagnostics(push_server):
    client, app, _, publisher, transport, settings = push_server
    reg = register(push_server)
    assert confirm(push_server, reg).status_code == 200
    publish(client, publisher)
    transport.results = [(429, "120"), OSError("secret-url-and-message"), (201, "")]
    assert app.state.worker.run_one()
    row = job_rows(app)[-1]
    assert row["status"] == "pending" and row["due"] >= time.time() + 119
    due_now(app)
    assert app.state.worker.run_one()
    assert job_rows(app)[-1]["diagnostic"] == "network_error"
    due_now(app)
    claimed = app.state.worker.claim()
    assert claimed is not None
    # Simulate process termination after claim; a new worker recovers the expired lease.
    with app.state.db.transaction() as con:
        con.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (claimed["id"],))
    resumed = Worker(Database(app.state.db.path), settings, transport)
    assert resumed.run_one()
    row = job_rows(app)[-1]
    assert row["status"] == "accepted" and row["attempts"] == 4
    publish(client, publisher)
    transport.results = [(410, "")]
    assert resumed.run_one()
    assert job_rows(app)[-1]["diagnostic"] == "subscription_gone"
    with app.state.db.transaction(write=False) as con:
        assert con.execute("SELECT status FROM subscriptions").fetchone()[0] == "disabled"
    assert all("secret" not in (row["diagnostic"] or "") for row in job_rows(app))


def test_failure_does_not_block_other_recipient_and_retry_bound(push_server):
    client, app, _, publisher, transport, settings = push_server
    assert confirm(push_server, register(push_server)).status_code == 200
    assert confirm(push_server, register(push_server, user="bob"), user="bob").status_code == 200
    publish(client, publisher, publication("alice", "bob"))
    worker = Worker(app.state.db, replace(settings, max_attempts=2), transport)
    transport.results = [(503, ""), (201, ""), (503, "")]
    assert worker.run_one() and worker.run_one()
    assert [r["status"] for r in job_rows(app)[-2:]] == ["pending", "accepted"]
    due_now(app)
    assert worker.run_one()
    assert job_rows(app)[-2]["diagnostic"] == "attempts_exhausted"


def test_state_update_stale_jobs_expiry_and_oversize_fallback(push_server, monkeypatch):
    client, app, users, publisher, transport, _ = push_server
    assert confirm(push_server, register(push_server)).status_code == 200
    assert confirm(push_server, register(push_server, delivery_type="webpush")).status_code == 200
    assert confirm(push_server, register(push_server, user="bob"), user="bob").status_code == 200
    req = publication("alice", "bob")
    req["notification"]["body"] = "x" * 4000
    n = publish(client, publisher, req)
    assert app.state.worker.run_one()
    envelope = transport.sent[-1][1]
    assert envelope["reference"] == {"from_server": n["from_server"], "notification_id": n["id"]}
    assert "state" not in envelope and isinstance(envelope["revision"], str)
    answer = response(n)
    assert (
        client.post("/v1/responses", json=answer, headers=auth(users["alice"])).status_code == 200
    )
    while app.state.worker.run_one():
        pass
    bob_messages = [payload for sub, payload, _ in transport.sent if sub["user"] == "bob"]
    assert bob_messages[-1]["kind"] == "notification"
    alice_messages = [payload for sub, payload, _ in transport.sent if sub["user"] == "alice"]
    assert alice_messages[-1]["kind"] == "state_update"
    # Small answered notifications include responded=true and no active actions.
    req = publication("alice", "bob")
    req["notification"]["expires_at"] = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    n = publish(client, publisher, req)
    assert (
        client.post("/v1/responses", json=response(n), headers=auth(users["alice"])).status_code
        == 200
    )
    monkeypatch.setattr(time, "time_ns", lambda: int((time.time() + 120) * 1e9))
    while app.state.worker.run_one():
        pass
    sub, envelope, _ = transport.sent[-1]
    assert sub["user"] == "alice" and sub["type"] == "unifiedpush"
    assert envelope["state"]["responded"] is True
    assert envelope["state"]["notification"]["actions"] == []
    assert all(
        payload.get("state", {}).get("notification", {}).get("id") != n["id"]
        for sub, payload, _ in transport.sent
        if sub["user"] == "bob"
    )


def test_retry_after_date_and_configuration_failure(push_server):
    client, app, _, publisher, transport, _ = push_server
    assert retry_delay("120", 1) >= 120
    assert retry_delay(format_datetime(datetime.now(UTC) + timedelta(minutes=5)), 1) >= 298
    assert 2 <= retry_delay("invalid", 1) < 3
    assert confirm(push_server, register(push_server)).status_code == 200
    publish(client, publisher)
    transport.results = [(403, "")]
    assert app.state.worker.run_one()
    assert job_rows(app)[-1]["diagnostic"] == "push_authentication_error"


def test_log_redaction(push_server, capsys):
    from loguru import logger

    logger.bind(
        event="push_result", endpoint="https://secret", token="secret", text="message-content"
    ).error("Bearer secret message-content")
    output = capsys.readouterr().err
    assert "secret" not in output and "message-content" not in output
    assert "push_result" in output


def test_dns_bounded_and_numeric_address_does_not_resolve(monkeypatch):
    resolver = Mock()
    resolver.resolve.side_effect = [["8.8.8.8"], dns.resolver.NoAnswer()]
    factory = Mock(return_value=resolver)
    monkeypatch.setattr("notifyr.push.dns.resolver.Resolver", factory)
    assert resolve_addresses("push.example", 443)[0][-1] == ("8.8.8.8", 443)
    assert resolver.resolve.call_count == 2
    resolver.resolve.assert_any_call("push.example", "A", lifetime=2, search=False)
    resolver.resolve.assert_any_call("push.example", "AAAA", lifetime=2, search=False)
    assert resolve_addresses("2001:4860:4860::8888", 443)[0][0] == socket.AF_INET6
    assert factory.call_count == 1
    resolver.resolve.side_effect = dns.resolver.LifetimeTimeout()
    with pytest.raises(OSError, match="Push DNS lookup failed"):
        resolve_addresses("push.example", 443)


def test_stale_lease_cannot_disable_subscription_or_overwrite_result(push_server):
    client, app, _, publisher, _, settings = push_server
    assert confirm(push_server, register(push_server)).status_code == 200
    publish(client, publisher)
    worker = app.state.worker
    stale = worker.claim()
    with app.state.db.transaction() as con:
        con.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (stale["id"],))
    resumed = Worker(app.state.db, settings, LocalTransport())
    assert resumed.run_one()
    worker.finish(stale, "failed", "subscription_gone", disable=True)
    assert job_rows(app)[-1]["status"] == "accepted"
    with app.state.db.transaction(write=False) as con:
        assert con.execute("SELECT status FROM subscriptions").fetchone()[0] == "active"


def test_push_failure_does_not_invalidate_accepted_response(push_server):
    client, app, users, publisher, transport, _ = push_server
    assert confirm(push_server, register(push_server)).status_code == 200
    n = publish(client, publisher)
    answer = response(n)
    accepted = client.post("/v1/responses", json=answer, headers=auth(users["alice"]))
    assert accepted.status_code == 200
    transport.results = [(403, ""), (410, "")]
    while app.state.worker.run_one():
        pass
    assert client.post("/v1/responses", json=answer, headers=auth(users["alice"])).json() == (
        accepted.json()
    )
    assert (
        client.get("/v1/apps/me/responses", headers=auth(publisher)).json()["items"][0][
            "response_id"
        ]
        == answer["response_id"]
    )
    assert (
        client.get(
            f"/v1/notifications/{n['id']}?from_server={n['from_server']}",
            headers=auth(users["alice"]),
        ).json()["responded"]
        is True
    )


def test_lifespan_worker_delivers_challenge_and_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "notifyr.push.resolve_addresses",
        lambda *a: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    delivered = threading.Event()

    class SignallingTransport(LocalTransport):
        def send(self, sub, payload, ttl):
            result = super().send(sub, payload, ttl)
            delivered.set()
            return result

    transport = SignallingTransport()
    app = create_app(
        Settings(database=tmp_path / "db", push_origins=("https://push.example",)),
        transport=transport,
    )
    with TestClient(app) as client:
        token = app.state.db.provision("recipient", "alice", [])
        _, public, secret = keypair()
        installation = uid()
        registered = client.put(
            f"/v1/subscriptions/{installation}",
            json={
                "delivery_type": "unifiedpush",
                "endpoint": "https://push.example/test",
                "p256dh": public,
                "auth": secret,
            },
            headers=auth(token),
        )
        assert registered.status_code == 200
        assert delivered.wait(5)
        challenge = transport.sent[0][1]
        assert (
            client.post(
                f"/v1/subscriptions/{installation}/confirm",
                json={"version": registered.json()["version"], "challenge": challenge["challenge"]},
                headers=auth(token),
            ).status_code
            == 200
        )
        assert client.get("/ready").status_code == 200
    assert app.state.worker.stopping.is_set() and app.state.worker_task.done()
