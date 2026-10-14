import json
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from notifyr import cli


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        self.server.requests.append((self.path, dict(self.headers), body))
        status, payload = self.server.response
        data = json.dumps(payload).encode()
        self.send_response(status)
        for name, value in self.server.extra_headers:
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def ping_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    server.response = (200, {"id": "42"})
    server.extra_headers = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def run(ping_server, monkeypatch, capsys, argv, *, response=None):
    if response is not None:
        ping_server.response = response
    port = ping_server.server_address[1]
    monkeypatch.setenv("NOTIFYR_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("NOTIFYR_PUBLISHER_TOKEN", "secret-token")
    code = cli.main(["ping", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_ping_publishes_recipient_body_and_title(ping_server, monkeypatch, capsys):
    code, out, err = run(ping_server, monkeypatch, capsys, ["--device", "alice", "--msg", "hi"])
    assert code == 0 and err == "" and out.strip() == "42"
    path, headers, body = ping_server.requests[0]
    assert path == "/v1/notifications"
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["Content-Type"] == "application/json"
    assert body["recipient_user_ids"] == ["alice"]
    assert body["notification"]["title"] == "Notifyr ping"
    assert body["notification"]["body"] == "hi"
    assert uuid.UUID(body["request_id"])


def test_buttons_ordered_unique_ids_and_text_reply(ping_server, monkeypatch, capsys):
    run(
        ping_server,
        monkeypatch,
        capsys,
        ["--device", "a", "--msg", "m", "--button", "Accept", "--button", "Deny", "--text-reply"],
    )
    actions = ping_server.requests[0][2]["notification"]["actions"]
    assert [action["label"] for action in actions] == ["Accept", "Deny", "Reply"]
    assert [action["kind"] for action in actions] == ["BUTTON", "BUTTON", "TEXT_REPLY"]
    ids = [action["id"] for action in actions]
    assert len(set(ids)) == 3 and all(ids)


def test_urgency_accepted_but_never_sent(ping_server, monkeypatch, capsys):
    code, _, _ = run(
        ping_server, monkeypatch, capsys, ["--device", "a", "--msg", "m", "--urgency", "high"]
    )
    assert code == 0
    assert "urgency" not in json.dumps(ping_server.requests[0][2])


def test_each_ping_uses_fresh_request_id(ping_server, monkeypatch, capsys):
    run(ping_server, monkeypatch, capsys, ["--device", "a", "--msg", "m"])
    run(ping_server, monkeypatch, capsys, ["--device", "a", "--msg", "m"])
    first, second = (request[2]["request_id"] for request in ping_server.requests)
    assert first != second


def test_blank_and_excess_actions_rejected_before_publish(ping_server, monkeypatch, capsys):
    cases = [
        ["--device", " ", "--msg", "m"],
        ["--device", "a", "--msg", "   "],
        ["--device", "a", "--msg", "m", "--button", " "],
        ["--device", "a", "--msg", "m", *sum((["--button", f"b{i}"] for i in range(9)), [])],
        [
            "--device",
            "a",
            "--msg",
            "m",
            *sum((["--button", f"b{i}"] for i in range(8)), []),
            "--text-reply",
        ],
    ]
    for argv in cases:
        code, out, err = run(ping_server, monkeypatch, capsys, argv)
        assert code == 1 and out == "" and err.startswith("error: ")
    assert ping_server.requests == []


def test_eight_buttons_is_the_boundary(ping_server, monkeypatch, capsys):
    argv = ["--device", "a", "--msg", "m", *sum((["--button", f"b{i}"] for i in range(8)), [])]
    code, _, _ = run(ping_server, monkeypatch, capsys, argv)
    assert code == 0
    assert len(ping_server.requests[0][2]["notification"]["actions"]) == 8


@pytest.mark.parametrize(
    "url",
    ["http://example.com", "https://user:pass@example.com", "https://example.com?a=1", "ftp://x"],
)
def test_insecure_or_malformed_urls_rejected(monkeypatch, capsys, url):
    monkeypatch.setenv("NOTIFYR_URL", url)
    monkeypatch.setenv("NOTIFYR_PUBLISHER_TOKEN", "secret-token")
    assert cli.main(["ping", "--device", "a", "--msg", "m"]) == 1
    assert capsys.readouterr().err.strip() == "error: invalid_url"


def test_missing_configuration_reports_stable_code(monkeypatch, capsys):
    monkeypatch.delenv("NOTIFYR_URL", raising=False)
    monkeypatch.delenv("NOTIFYR_PUBLISHER_TOKEN", raising=False)
    assert cli.main(["ping", "--device", "a", "--msg", "m"]) == 1
    assert capsys.readouterr().err.strip() == "error: missing_configuration"


def test_api_error_is_stable_and_does_not_leak_credentials(ping_server, monkeypatch, capsys):
    code, out, err = run(
        ping_server,
        monkeypatch,
        capsys,
        ["--device", "a", "--msg", "m"],
        response=(403, {"error": "recipient_not_authorized"}),
    )
    assert code == 1 and out == ""
    assert err.strip() == "error: recipient_not_authorized"
    assert "secret-token" not in err


def test_redirect_is_not_followed(ping_server, monkeypatch, capsys):
    ping_server.extra_headers = [("Location", "https://evil.example/v1/notifications")]
    code, _, err = run(
        ping_server,
        monkeypatch,
        capsys,
        ["--device", "a", "--msg", "m"],
        response=(302, {}),
    )
    assert code == 1 and err.strip() == "error: http_302"


def test_network_failure_is_stable(monkeypatch, capsys):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("NOTIFYR_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("NOTIFYR_PUBLISHER_TOKEN", "secret-token")
    assert cli.main(["ping", "--device", "a", "--msg", "m"]) == 1
    assert capsys.readouterr().err.strip() == "error: network_error"


def test_device_is_required(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        cli.main(["ping", "--msg", "m"])
