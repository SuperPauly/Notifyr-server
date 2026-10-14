"""Publish Notifyr pings through the authenticated HTTPS `/v1/notifications` route."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

TITLE = "Notifyr ping"
MAX_ACTIONS = 8
LOOPBACK_HOSTS = {"localhost", "::1"}
# ponytail: 30s fixed timeout; expose a flag if operators need to tune it.
TIMEOUT = 30.0


class CliError(Exception):
    """A stable, credential-free failure surfaced to the operator."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: the bearer token must not leave the configured host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def loopback(hostname: str) -> bool:
    return hostname in LOOPBACK_HOSTS or hostname.startswith("127.")


def server_url(raw: str) -> str:
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CliError("invalid_url")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CliError("invalid_url")
    if parsed.scheme != "https" and not loopback(parsed.hostname):
        raise CliError("invalid_url")
    return raw.rstrip("/")


def load_config() -> tuple[str, str]:
    url = os.getenv("NOTIFYR_URL", "").strip()
    token = os.getenv("NOTIFYR_PUBLISHER_TOKEN", "").strip()
    if not url or not token:
        raise CliError("missing_configuration")
    return server_url(url), token


def build_payload(args: argparse.Namespace) -> dict[str, object]:
    devices = list(args.device)
    if not devices or any(not device.strip() for device in devices):
        raise CliError("invalid_recipient")
    if not args.msg.strip():
        raise CliError("invalid_message")
    if any(not label.strip() for label in args.button):
        raise CliError("invalid_button")
    if len(args.button) + int(args.text_reply) > MAX_ACTIONS:
        raise CliError("action_limit")
    actions = [{"id": uuid.uuid4().hex, "label": label, "kind": "BUTTON"} for label in args.button]
    if args.text_reply:
        actions.append({"id": uuid.uuid4().hex, "label": "Reply", "kind": "TEXT_REPLY"})
    notification: dict[str, object] = {"title": TITLE, "body": args.msg}
    if actions:
        notification["actions"] = actions
    return {
        "request_id": str(uuid.uuid4()),
        "recipient_user_ids": devices,
        "notification": notification,
    }


def error_code(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read())
    except (ValueError, OSError):
        return f"http_{exc.code}"
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    return f"http_{exc.code}"


def send(url: str, token: str, payload: dict[str, object]) -> str:
    request = urllib.request.Request(
        url + "/v1/notifications",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise CliError(error_code(exc)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise CliError("network_error") from exc
    notification_id = result.get("id") if isinstance(result, dict) else None
    if not isinstance(notification_id, str) or not notification_id:
        raise CliError("invalid_response")
    return notification_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="notifyr", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    ping = sub.add_parser("ping", help="Publish a ping notification")
    ping.add_argument("--device", action="append", required=True, metavar="USER")
    ping.add_argument("--msg", required=True, metavar="BODY")
    ping.add_argument("--button", action="append", default=[], metavar="LABEL")
    ping.add_argument("--text-reply", action="store_true")
    ping.add_argument("--urgency", default=None, help="Accepted for compatibility; never sent")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        url, token = load_config()
        notification_id = send(url, token, build_payload(args))
    except CliError as exc:
        print(f"error: {exc.code}", file=sys.stderr)
        return 1
    print(notification_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
