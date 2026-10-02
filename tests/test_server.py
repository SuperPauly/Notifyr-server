import copy
import json
import re
import shlex
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from notifyr import notifications_pb2 as pb
from notifyr.api import create_app
from notifyr.config import Settings
from notifyr.contract import json_message
from notifyr.db import Database, digest


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def server(tmp_path):
    app = create_app(Settings(database=tmp_path / "db.sqlite", worker_enabled=False))
    with TestClient(app) as client:
        db = app.state.db
        users = {user: db.provision("recipient", user, []) for user in ("alice", "bob", "eve")}
        apps = {
            name: db.provision("publisher", name, ["alice", "bob"]) for name in ("app", "other")
        }
        yield client, app, users, apps


def auth(token):
    return {"Authorization": "Bearer " + token}


def publication(*users, actions=True):
    return {
        "request_id": uid(),
        "recipient_user_ids": list(users or ("alice",)),
        "notification": {
            "title": "Test",
            "body": "body",
            "actions": [
                {"id": "yes", "label": "Yes", "kind": "BUTTON"},
                {"id": "reply", "label": "Reply", "kind": "TEXT_REPLY"},
            ]
            if actions
            else [],
        },
    }


def publish(client, token, payload=None):
    result = client.post("/v1/notifications", json=payload or publication(), headers=auth(token))
    assert result.status_code == 200, result.text
    return result.json()


def response(n, *, action="yes", response_id=None, **kwargs):
    return {
        "response_id": response_id or uid(),
        "notification_id": n["id"],
        "from_server": n["from_server"],
        "action_id": action,
        **kwargs,
    }


def test_startup_auth_revocation_hash_only(server):
    client, app, users, apps = server
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").status_code == 200
    info = client.get("/v1/server-info").json()
    assert uuid.UUID(info["server_id"])
    assert len(info["vapid_public_key"]) == 87
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/me", headers=auth("wrong")).status_code == 401
    assert client.get("/v1/me", headers=auth(users["alice"])).json() == {
        "scope": "recipient",
        "user_id": "alice",
    }
    assert client.get("/v1/me", headers=auth(apps["app"])).json() == {
        "scope": "publisher",
        "app_id": "app",
    }
    assert (
        client.post(
            "/v1/notifications", json=publication(), headers=auth(users["alice"])
        ).status_code
        == 403
    )
    with app.state.db.transaction() as con:
        stored = con.execute("SELECT hash FROM tokens").fetchall()
        assert all(users["alice"] != row[0] for row in stored)
        con.execute("UPDATE tokens SET revoked=1 WHERE hash=?", (digest(users["alice"]),))
    assert client.get("/v1/me", headers=auth(users["alice"])).status_code == 401


def test_authorized_fanout_dedup_idempotency_and_isolation(server):
    client, app, users, apps = server
    req = publication("alice", "bob", "alice")
    n = publish(client, apps["app"], req)
    assert publish(client, apps["app"], req) == n
    reordered = copy.deepcopy(req)
    reordered["recipient_user_ids"] = ["bob", "alice"]
    assert publish(client, apps["app"], reordered) == n
    assert n["id"] == "1" and n["from_app"] == "app" and n["created_at"].endswith("Z")
    assert (
        client.get(
            "/v1/notifications/1",
            params={"from_server": n["from_server"]},
            headers=auth(users["alice"]),
        ).json()["responded"]
        is False
    )
    assert (
        client.get(
            "/v1/notifications/1",
            params={"from_server": n["from_server"]},
            headers=auth(users["eve"]),
        ).status_code
        == 404
    )
    assert client.get(
        "/v1/notifications/1", params={"from_server": uid()}, headers=auth(users["alice"])
    ).json() == {"error": "server_mismatch"}
    changed = copy.deepcopy(req)
    changed["notification"]["title"] = "Changed"
    assert (
        client.post("/v1/notifications", json=changed, headers=auth(apps["app"])).status_code == 409
    )
    assert (
        client.post(
            "/v1/notifications", json=publication("eve"), headers=auth(apps["app"])
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/notifications", json=publication("unknown"), headers=auth(apps["app"])
        ).status_code
        == 403
    )
    with app.state.db.transaction(write=False) as con:
        assert con.execute("SELECT count(*) FROM recipients").fetchone()[0] == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("from_server", "spoof"),
        ("from_app", "spoof"),
        ("created_at", "2030-01-01T00:00:00Z"),
        ("id", "42"),
    ],
)
def test_source_spoofing(server, field, value):
    client, _, _, apps = server
    req = publication()
    req["notification"][field] = value
    assert client.post("/v1/notifications", json=req, headers=auth(apps["app"])).json() == {
        "error": "source_spoofing"
    }


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.test/x",
        "//evil.test",
        "javascript:alert(1)",
        "\\evil",
        "/../x",
        "/%2e%2e/x",
        "/%252f%252fevil",
        "/x\n",
        "//[invalid",
    ],
)
def test_open_path_validation(server, path):
    client, _, _, apps = server
    req = publication()
    req["notification"]["open_path"] = path
    assert client.post("/v1/notifications", json=req, headers=auth(apps["app"])).status_code == 422


def test_request_actions_utf8_limits_and_presence(server):
    client, _, users, apps = server
    for mutate in [
        lambda p: p.update(request_id="bad"),
        lambda p: p["notification"].update(title="😀" * 65),
        lambda p: p["notification"].update(body="x" * 16385),
        lambda p: p["notification"].update(actions=p["notification"]["actions"] * 2),
        lambda p: p["notification"]["actions"][0].update(kind="KIND_UNSPECIFIED"),
        lambda p: p["notification"].update(expires_at="invalid"),
    ]:
        req = publication()
        mutate(req)
        assert (
            client.post("/v1/notifications", json=req, headers=auth(apps["app"])).status_code == 422
        )
    n = publish(client, apps["app"])
    for payload in [
        response(n, text=""),
        response(n, text=None),
        response(n, action="missing"),
        response(n, action="reply"),
        response(n, action="reply", text="  "),
        response(n, action="reply", text="😀" * 1025),
    ]:
        assert (
            client.post("/v1/responses", json=payload, headers=auth(users["alice"])).status_code
            == 422
        )
    req = response(n, action="reply", text="😀" * 1024)
    receipt = client.post("/v1/responses", json=req, headers=auth(users["alice"])).json()
    assert receipt["accepted_at"].endswith("Z")
    assert client.post("/v1/responses", json=req, headers=auth(users["alice"])).json() == receipt
    changed = req | {"text": "changed"}
    assert client.post("/v1/responses", json=changed, headers=auth(users["alice"])).json() == {
        "error": "idempotency_conflict"
    }
    assert client.post("/v1/responses", json=response(n), headers=auth(users["alice"])).json() == {
        "error": "already_responded"
    }
    results = client.get("/v1/apps/me/responses", headers=auth(apps["app"])).json()
    assert results["items"][0]["text"] == "😀" * 1024
    assert results["items"][0]["responder_user_id"] == "alice"
    assert client.get("/v1/apps/me/responses", headers=auth(apps["other"])).json()["items"] == []


def test_button_optional_text_and_independent_recipients(server):
    client, _, users, apps = server
    n = publish(client, apps["app"], publication("alice", "bob"))
    req = response(n)
    assert client.post("/v1/responses", json=req, headers=auth(users["eve"])).status_code == 404
    assert (
        client.post(
            "/v1/responses", json=req | {"from_server": uid()}, headers=auth(users["alice"])
        ).status_code
        == 409
    )
    assert client.post("/v1/responses", json=req, headers=auth(users["alice"])).status_code == 200
    assert client.post("/v1/responses", json=req, headers=auth(users["bob"])).status_code == 200
    items = client.get("/v1/apps/me/responses", headers=auth(apps["app"])).json()["items"]
    assert len(items) == 2 and all("text" not in item for item in items)
    n = publish(client, apps["app"], publication(actions=False))
    assert client.post("/v1/responses", json=response(n), headers=auth(users["alice"])).json() == {
        "error": "unknown_action"
    }


def test_concurrent_publish_and_two_device_responses(server):
    client, app, users, apps = server
    req = publication("alice", "bob")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: publish(client, apps["app"], req), range(8)))
    assert all(result == results[0] for result in results)
    n = results[0]
    replies = [response(n), response(n, action="reply", text="reply")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda p: client.post("/v1/responses", json=p, headers=auth(users["alice"])),
                replies,
            )
        )
    assert sorted(r.status_code for r in results) == [200, 409]
    winning = replies[next(i for i, r in enumerate(results) if r.status_code == 200)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: client.post("/v1/responses", json=winning, headers=auth(users["alice"])),
                range(8),
            )
        )
    assert all(r.status_code == 200 and r.json() == results[0].json() for r in results)
    with app.state.db.transaction(write=False) as con:
        assert con.execute("SELECT count(*) FROM notifications").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM responses").fetchone()[0] == 1


def test_expiry_identical_retries_remain_valid(server, monkeypatch):
    client, _, users, apps = server
    now = time.time_ns()
    req = publication("alice", "bob")
    ts = pb.Notification().expires_at
    ts.FromNanoseconds(now + 60_000_000_000)
    req["notification"]["expires_at"] = ts.ToJsonString()
    n = publish(client, apps["app"], req)
    answer = response(n)
    receipt = client.post("/v1/responses", json=answer, headers=auth(users["alice"])).json()
    monkeypatch.setattr(time, "time_ns", lambda: now + 60_000_000_000)
    assert publish(client, apps["app"], req) == n
    assert client.post("/v1/responses", json=answer, headers=auth(users["alice"])).json() == receipt
    assert (
        client.post("/v1/responses", json=response(n), headers=auth(users["bob"])).status_code
        == 410
    )
    assert client.post(
        "/v1/notifications", json=req | {"request_id": uid()}, headers=auth(apps["app"])
    ).json() == {"error": "invalid_expiry"}


def test_inbox_pagination_older_response_and_publisher_polling(server):
    client, _, users, apps = server
    old = publish(client, apps["app"])
    publish(client, apps["app"])
    first = client.get("/v1/inbox?limit=1", headers=auth(users["alice"])).json()
    assert first["has_more"] and first["items"][0]["state"]["responded"] is False
    assert (
        client.post("/v1/responses", json=response(old), headers=auth(users["alice"])).status_code
        == 200
    )
    second = client.get(
        "/v1/inbox", params={"cursor": first["cursor"], "limit": 1}, headers=auth(users["alice"])
    ).json()
    third = client.get(
        "/v1/inbox", params={"cursor": second["cursor"]}, headers=auth(users["alice"])
    ).json()
    assert third["items"][0]["state"]["notification"]["id"] == old["id"]
    assert third["items"][0]["state"]["responded"] is True
    assert client.get("/v1/inbox", headers=auth(users["eve"])).json()["items"] == []
    assert client.get("/v1/inbox?cursor=-1", headers=auth(users["alice"])).status_code == 422
    first_poll = client.get("/v1/apps/me/responses", headers=auth(apps["app"])).json()
    assert client.get("/v1/apps/me/responses", headers=auth(apps["app"])).json() == first_poll
    assert (
        client.get(
            "/v1/apps/me/responses",
            params={"cursor": first_poll["cursor"]},
            headers=auth(apps["app"]),
        ).json()["items"]
        == []
    )


def test_int64_restart_and_database_backup(server, tmp_path):
    client, app, _, apps = server
    info = app.state.db.info()
    with app.state.db.transaction() as con:
        con.execute("UPDATE meta SET value=? WHERE key='next_id'", (str(2**53 + 1),))
    n = publish(client, apps["app"])
    assert n["id"] == "9007199254740993"
    reopened = Database(app.state.db.path)
    reopened.initialize()
    assert reopened.info() == info
    next_n = publish(client, apps["app"])
    assert next_n["id"] == "9007199254740994"
    source = reopened.connect()
    target = Database(tmp_path / "backup.sqlite").connect()
    source.backup(target)
    target.close()
    source.close()
    restored = Database(tmp_path / "backup.sqlite")
    restored.initialize()
    assert restored.info() == info
    message = pb.RespondRequest(response_id=uid(), notification_id=2**53 + 1, text="")
    encoded = json_message(message)
    assert encoded["notification_id"] == n["id"] and "text" in encoded
    assert "text" not in json_message(pb.RespondRequest())
    assert json_message(pb.NotificationState())["responded"] is False


def test_limits_and_accurate_openapi(tmp_path):
    settings = replace(
        Settings(database=tmp_path / "db", worker_enabled=False),
        max_recipients=2,
        max_request_bytes=1024,
        cors_origins=("https://client.example",),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        db = app.state.db
        db.provision("recipient", "alice", [])
        token = db.provision("publisher", "app", ["alice"])
        assert client.post("/v1/notifications", content="x" * 1025).status_code == 413
        assert (
            client.post("/v1/notifications", content=iter([b"x" * 600, b"y" * 600])).status_code
            == 413
        )
        assert client.post(
            "/v1/notifications", json=publication("alice", "alice", "alice"), headers=auth(token)
        ).json() == {"error": "fanout_limit"}
        schema = client.get("/openapi.json").json()
        assert set(schema["paths"]) == {
            "/health",
            "/ready",
            "/v1/server-info",
            "/v1/me",
            "/v1/notifications",
            "/v1/notifications/{id}",
            "/v1/inbox",
            "/v1/responses",
            "/v1/apps/me/responses",
            "/v1/subscriptions/{installation_id}",
            "/v1/subscriptions/{installation_id}/confirm",
        }
        assert schema["paths"]["/v1/responses"]["post"]["security"] == [{"HTTPBearer": []}]
        assert (
            schema["components"]["schemas"]["Notification"]["properties"]["id"]["type"] == "string"
        )
        allowed = client.options(
            "/v1/me",
            headers={"Origin": "https://client.example", "Access-Control-Request-Method": "GET"},
        )
        denied = client.options(
            "/v1/me",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
        )
        assert allowed.headers["access-control-allow-origin"] == "https://client.example"
        assert "access-control-allow-origin" not in denied.headers


@pytest.mark.parametrize(
    "key,value",
    [
        ("server_id", "bad"),
        ("next_id", "0"),
        ("next_id", "1"),
        ("next_id", str(2**63 + 1)),
        ("vapid_public", "mismatch"),
        ("schema_version", "2"),
        (None, None),
    ],
)
def test_corrupt_identity_fails_closed(server, key, value):
    client, app, _, apps = server
    publish(client, apps["app"])
    with app.state.db.transaction() as con:
        if key is None:
            con.execute("DELETE FROM meta")
        else:
            con.execute("UPDATE meta SET value=? WHERE key=?", (value, key))
    with pytest.raises(RuntimeError, match="identity metadata"):
        Database(app.state.db.path).initialize()


def test_allocator_exhaustion_and_far_future_expiry(server):
    client, app, _, apps = server
    with app.state.db.transaction() as con:
        con.execute("UPDATE meta SET value=? WHERE key='next_id'", (str(2**63 - 1),))
    req = publication()
    req["notification"]["expires_at"] = "9999-12-31T23:59:59.999999999Z"
    n = publish(client, apps["app"], req)
    assert n["id"] == str(2**63 - 1)
    Database(app.state.db.path).initialize()
    assert publish(client, apps["app"], req) == n
    result = client.post("/v1/notifications", json=publication(), headers=auth(apps["app"]))
    assert result.status_code == 503 and result.json() == {"error": "id_space_exhausted"}


def test_publication_rolls_back_if_delivery_intent_cannot_persist(server, monkeypatch):
    client, app, _, apps = server

    def broken_enqueue(*args):
        raise RuntimeError("Simulated queue storage failure")

    monkeypatch.setattr(Database, "enqueue", broken_enqueue)
    with pytest.raises(RuntimeError, match="Simulated queue storage failure"):
        client.post("/v1/notifications", json=publication(), headers=auth(apps["app"]))
    with app.state.db.transaction(write=False) as con:
        assert Database.meta(con, "next_id") == "1"
        for table in ("notifications", "recipients", "changes", "jobs"):
            assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_response_rolls_back_if_delivery_intent_cannot_persist(server, monkeypatch):
    client, app, users, apps = server
    n = publish(client, apps["app"])

    def broken_enqueue(*args):
        raise RuntimeError("Simulated queue storage failure")

    monkeypatch.setattr(Database, "enqueue", broken_enqueue)
    with pytest.raises(RuntimeError, match="Simulated queue storage failure"):
        client.post("/v1/responses", json=response(n), headers=auth(users["alice"]))
    with app.state.db.transaction(write=False) as con:
        assert con.execute("SELECT count(*) FROM responses").fetchone()[0] == 0
        assert con.execute("SELECT responded FROM recipients").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM changes").fetchone()[0] == 1


def test_documented_curl_examples_use_actual_contract(server):
    client, app, users, apps = server
    doc = (Path(__file__).resolve().parents[1] / "API_CONTRACT.md").read_text()
    block = re.search(r"```bash\n(.*?)```", doc, re.S).group(1)
    values = {
        "BASE": "",
        "PUBLISHER_TOKEN": apps["app"],
        "RECIPIENT_TOKEN": users["alice"],
        "SERVER_ID": app.state.db.info()["server_id"],
        "NOTIFICATION_ID": "2",
    }
    commands = block.replace("\\\n", " ").splitlines()
    results = []
    for command in commands:
        if not command.startswith("curl "):
            continue
        for name, value in values.items():
            command = command.replace("$" + name, value)
        args = shlex.split(command)
        path = args[2]
        headers, data = {}, None
        for index, arg in enumerate(args):
            if arg == "-H":
                key, value = args[index + 1].split(":", 1)
                headers[key] = value.strip()
            elif arg == "-d":
                data = json.loads(args[index + 1])
        result = client.request("POST" if data else "GET", path, headers=headers, json=data)
        assert result.status_code == 200, result.text
        results.append(result.json())
    assert len(results) == 10
    assert results[5] == results[6]  # accepted response and identical retry
    assert results[7]["items"][0]["action_id"] == "approve"


def test_admin_backup_cli_preserves_identity(tmp_path, monkeypatch, capsys):
    from notifyr.admin import main

    path, backup = tmp_path / "db", tmp_path / "backup"
    monkeypatch.setenv("NOTIFYR_DB", str(path))
    monkeypatch.setattr("sys.argv", ["notifyr-admin", "init"])
    main()
    original = json.loads(capsys.readouterr().out)
    monkeypatch.setattr("sys.argv", ["notifyr-admin", "recipient", "alice"])
    main()
    token = capsys.readouterr().out.strip()
    monkeypatch.setattr("sys.argv", ["notifyr-admin", "backup", str(backup)])
    main()
    assert backup.stat().st_mode & 0o777 == 0o600
    restored = Database(backup)
    restored.initialize()
    assert restored.info() == original
    assert restored.authenticate(token)["user"] == "alice"
    with restored.transaction(write=False) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
