"""Owned subscriptions, encrypted Web Push and a durable leased delivery worker."""

import asyncio
import base64
import binascii
import http.client
import ipaddress
import re
import secrets
import socket
import ssl
import threading
import time
import uuid
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import dns.exception
import dns.resolver
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from loguru import logger
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from notifyr.config import Settings
from notifyr.contract import Error
from notifyr.db import Database, digest
from notifyr.service import Service, canonical, uuid_value


def resolve_addresses(host: str, port: int) -> list[tuple]:
    """Bound DNS lookup lifetime; use numeric literals without resolving them again."""
    try:
        address = ipaddress.ip_address(host)
        family = socket.AF_INET if address.version == 4 else socket.AF_INET6
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (str(address), port))]
    except ValueError:
        pass
    resolver = dns.resolver.Resolver()
    addresses: list[tuple] = []
    for record_type, family in (("A", socket.AF_INET), ("AAAA", socket.AF_INET6)):
        try:
            answer = resolver.resolve(host, record_type, lifetime=2, search=False)
            addresses.extend(
                (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (str(r), port)) for r in answer
            )
        except dns.resolver.NoAnswer:
            continue
        except dns.exception.DNSException as exc:
            raise OSError("Push DNS lookup failed") from exc
    return addresses


class Policy:
    """Resolve once, validate every answer, connect to a checked numeric address.

    TLS still uses the original hostname for SNI/certificate verification. No
    redirects, environment proxies, or second hostname resolution are permitted.
    """

    def __init__(self, settings: Settings):
        self.allowed = set(settings.push_origins)
        self.private = set(settings.private_push_origins)
        for origin in self.allowed | self.private:
            if self.origin(origin) != origin:
                raise ValueError("Push origins must be canonical HTTPS origins without a path")

    @staticmethod
    def origin(endpoint: str) -> str:
        try:
            url = urlsplit(endpoint)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.fragment
                or len(endpoint) > 2048
                or any(ord(ch) < 33 or ord(ch) > 126 for ch in endpoint)
                or "\\" in endpoint
                or "%" in url.netloc
            ):
                raise ValueError
            port = url.port or 443
            host = url.hostname.lower()
            rendered = f"[{host}]" if ":" in host else host
            return f"https://{rendered}" + (f":{port}" if port != 443 else "")
        except ValueError as exc:
            raise Error(422, "invalid_push_endpoint") from exc

    def validate(self, endpoint: str) -> str:
        origin = self.origin(endpoint)
        if origin not in self.allowed | self.private:
            raise Error(422, "push_origin_not_allowed")
        return origin

    def addresses(self, endpoint: str) -> list[tuple]:
        origin = self.validate(endpoint)
        url = urlsplit(endpoint)
        addresses = resolve_addresses(url.hostname or "", url.port or 443)
        if not addresses:
            raise OSError("No destination addresses")
        for _, _, _, _, sockaddr in addresses:
            address = ipaddress.ip_address(sockaddr[0])
            mapped = getattr(address, "ipv4_mapped", None)
            if mapped:
                address = mapped
            forbidden = (
                address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_unspecified
            )
            if forbidden or (not address.is_global and origin not in self.private):
                raise Error(422, "push_destination_blocked")
        return addresses[:4]


class PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, endpoint: str, policy: Policy, timeout: float):
        url = urlsplit(endpoint)
        super().__init__(
            url.hostname or "",
            url.port or 443,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self.endpoint = endpoint
        self.policy = policy
        self.connection_timeout = timeout
        self.tls_context = ssl.create_default_context()
        self.deadline = time.monotonic() + timeout

    def connect(self) -> None:
        addresses = self.policy.addresses(self.endpoint)
        for family, socktype, protocol, _, sockaddr in addresses:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Connection deadline exceeded")
            sock = socket.socket(family, socktype, protocol)
            try:
                sock.settimeout(remaining)
                sock.connect(sockaddr)
                if ipaddress.ip_address(sock.getpeername()[0]) != ipaddress.ip_address(sockaddr[0]):
                    raise OSError("Destination changed")
                self.sock = self.tls_context.wrap_socket(sock, server_hostname=self.host)
                self.sock.settimeout(self.connection_timeout)
                return
            except OSError:
                sock.close()
                if sockaddr == addresses[-1][4]:
                    raise


class PinnedSession(requests.Session):
    def __init__(self, policy: Policy):
        super().__init__()
        self.policy = policy
        self.trust_env = False

    def post(self, url, data=None, json=None, **kwargs):
        headers = kwargs.get("headers")
        timeout = kwargs.get("timeout", 10)
        self.policy.validate(url)
        endpoint = urlsplit(url)
        connection = PinnedConnection(url, self.policy, float(timeout))

        def abort() -> None:
            if connection.sock is not None:
                try:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()

        timer = threading.Timer(float(timeout), abort)
        timer.daemon = True
        timer.start()
        try:
            connection.request(
                "POST",
                (endpoint.path or "/") + ("?" + endpoint.query if endpoint.query else ""),
                body=data,
                headers=dict(headers or {}),
            )
            received = connection.getresponse()
            # Provider bodies can contain capability URLs, echoed content or secrets.
            # Never read them; diagnostics use only status and bounded Retry-After.
            result = requests.Response()
            result.status_code = received.status
            result.headers["Retry-After"] = received.getheader("Retry-After", "")[:128]
            result._content = b""
            result.reason = "push service response"
            return result
        finally:
            timer.cancel()
            connection.close()


class Transport:
    def __init__(self, db: Database, settings: Settings, policy: Policy):
        self.db = db
        self.settings = settings
        self.policy = policy

    def send(self, sub: dict, payload: str, ttl: int) -> tuple[int, str]:
        with self.db.transaction(write=False) as con:
            private = self.db.meta(con, "vapid_private")
        vapid = Vapid.from_der(private.encode())
        with PinnedSession(self.policy) as session:
            try:
                response = webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                    },
                    data=payload,
                    vapid_private_key=vapid,
                    vapid_claims={"sub": self.settings.vapid_subject},
                    content_encoding="aes128gcm",
                    timeout=self.settings.push_timeout,
                    ttl=ttl,
                    requests_session=session,
                )
            except WebPushException as exc:
                if exc.response is None:
                    raise Error(422, "push_configuration_error") from None
                response = exc.response
            return response.status_code, response.headers.get("Retry-After", "")


def decode_key(value: str, expected: int) -> bytes:
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value) or len(value) > 128:
            raise ValueError
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        if len(decoded) != expected:
            raise ValueError
        return decoded
    except (ValueError, binascii.Error) as exc:
        raise Error(422, "invalid_push_keys") from exc


class Subscriptions:
    def __init__(self, db: Database, settings: Settings, policy: Policy):
        self.db, self.settings, self.policy = db, settings, policy

    def register(self, user: str, installation: str, payload: dict) -> dict:
        installation = uuid_value(installation)
        self.policy.validate(payload["endpoint"])
        # Registration checks DNS too, but the mandatory check is repeated inside connect().
        try:
            self.policy.addresses(payload["endpoint"])
        except OSError as exc:
            raise Error(503, "push_destination_unavailable") from exc
        public = decode_key(payload["p256dh"], 65)
        decode_key(payload["auth"], 16)
        try:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
        except ValueError as exc:
            raise Error(422, "invalid_push_keys") from exc
        challenge = secrets.token_urlsafe(32)
        with self.db.transaction() as con:
            old = con.execute(
                "SELECT * FROM subscriptions WHERE installation=?", (installation,)
            ).fetchone()
            if old and old["user"] != user:
                raise Error(403, "subscription_not_owned")
            endpoint_owner = con.execute(
                "SELECT installation FROM subscriptions WHERE endpoint=?", (payload["endpoint"],)
            ).fetchone()
            if endpoint_owner and endpoint_owner[0] != installation:
                raise Error(409, "endpoint_already_registered")
            version = old["version"] + 1 if old else 1
            con.execute(
                "INSERT INTO subscriptions VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(installation) DO UPDATE SET version=excluded.version,"
                "type=excluded.type,endpoint=excluded.endpoint,p256dh=excluded.p256dh,"
                "auth=excluded.auth,status='pending',challenge_hash=excluded.challenge_hash,"
                "challenge_expires=excluded.challenge_expires",
                (
                    installation,
                    user,
                    version,
                    payload["delivery_type"],
                    payload["endpoint"],
                    payload["p256dh"],
                    payload["auth"],
                    "pending",
                    digest(challenge),
                    time.time() + self.settings.challenge_seconds,
                ),
            )
            envelope = {
                "version": 1,
                "kind": "registration_challenge",
                "installation_id": installation,
                "subscription_version": version,
                "challenge": challenge,
                "from_server": self.db.meta(con, "server_id"),
                "visible_notification": {
                    "title": "Verify Notifyr push registration",
                    "body": "Confirm this device in Notifyr.",
                },
            }
            con.execute(
                "INSERT INTO jobs(installation,version,user,kind,payload,due) VALUES (?,?,?,?,?,?)",
                (
                    installation,
                    version,
                    user,
                    "registration_challenge",
                    canonical(envelope),
                    time.time(),
                ),
            )
            return {"installation_id": installation, "version": version, "status": "pending"}

    def confirm(self, user: str, installation: str, version: int, challenge: str) -> dict:
        installation = uuid_value(installation)
        with self.db.transaction() as con:
            sub = self.owned(con, user, installation)
            if sub["version"] != version or sub["status"] != "pending":
                raise Error(409, "challenge_not_current")
            if sub["challenge_expires"] <= time.time():
                raise Error(410, "challenge_expired")
            if not secrets.compare_digest(sub["challenge_hash"], digest(challenge)):
                raise Error(422, "invalid_challenge")
            con.execute(
                "UPDATE subscriptions SET status='active',challenge_hash=NULL,"
                "challenge_expires=NULL WHERE installation=?",
                (installation,),
            )
            con.execute(
                "UPDATE jobs SET status='discarded',payload=NULL WHERE installation=? "
                "AND version=? AND kind='registration_challenge' AND status='pending'",
                (installation, version),
            )
            return {"installation_id": installation, "version": version, "status": "active"}

    @staticmethod
    def owned(con, user: str, installation: str):
        sub = con.execute(
            "SELECT * FROM subscriptions WHERE installation=? AND user=?", (installation, user)
        ).fetchone()
        if not sub:
            raise Error(404, "subscription_not_found")
        return sub

    def delete(self, user: str, installation: str) -> None:
        installation = uuid_value(installation)
        with self.db.transaction() as con:
            self.owned(con, user, installation)
            # Retain installation ownership/version tombstone; remove sensitive material.
            con.execute(
                "UPDATE subscriptions SET status='deleted',version=version+1,endpoint=NULL,"
                "p256dh=NULL,auth=NULL,challenge_hash=NULL,challenge_expires=NULL "
                "WHERE installation=?",
                (installation,),
            )
            con.execute(
                "UPDATE jobs SET status='discarded',payload=NULL WHERE installation=? "
                "AND status IN ('pending','processing')",
                (installation,),
            )


def retry_delay(value: str, attempt: int) -> float:
    delay = min(3600, 2 ** min(attempt, 12)) + secrets.randbelow(1000) / 1000
    try:
        if value.strip().isdigit():
            requested = float(value)
        else:
            requested = parsedate_to_datetime(value).timestamp() - time.time()
        return max(delay, max(0, requested))
    except (ValueError, TypeError, OverflowError):
        return delay


class Worker:
    def __init__(self, db: Database, settings: Settings, transport):
        self.db, self.settings, self.transport = db, settings, transport
        self.stopping = asyncio.Event()

    def claim(self) -> dict | None:
        now = time.time()
        with self.db.transaction() as con:
            con.execute(
                "UPDATE jobs SET status='failed',diagnostic='attempts_exhausted',payload=NULL "
                "WHERE attempts>=? AND (status='pending' OR "
                "(status='processing' AND lease_until<=?))",
                (self.settings.max_attempts, now),
            )
            row = con.execute(
                "SELECT * FROM jobs WHERE attempts<? AND "
                "((status='pending' AND due<=?) OR "
                "(status='processing' AND lease_until<=?)) ORDER BY id LIMIT 1",
                (self.settings.max_attempts, now, now),
            ).fetchone()
            if not row:
                return None
            token = str(uuid.uuid4())
            con.execute(
                "UPDATE jobs SET status='processing',attempts=attempts+1,lease_until=?,"
                "lease_token=? WHERE id=?",
                (now + self.settings.lease_seconds, token, row["id"]),
            )
            return dict(row) | {"lease_token": token, "attempts": row["attempts"] + 1}

    def prepare(self, job: dict) -> tuple[dict, str, int] | None:
        with self.db.transaction(write=False) as con:
            sub = con.execute(
                "SELECT * FROM subscriptions WHERE installation=?", (job["installation"],)
            ).fetchone()
            if not sub or sub["version"] != job["version"]:
                return None
            if job["kind"] == "registration_challenge":
                if sub["status"] != "pending" or sub["challenge_expires"] <= time.time():
                    return None
                ttl = max(0, int(sub["challenge_expires"] - time.time()))
                return dict(sub), job["payload"], ttl
            if sub["status"] != "active":
                return None
            row = Service.access(con, job["user"], job["notification"])
            state = Service.state(row)
            kind = job["kind"]
            if row["responded"]:
                if sub["type"] == "webpush":
                    return None
                kind = "state_update"
                state["notification"]["actions"] = []
            elif row["expires_ns"] is not None and time.time_ns() >= int(row["expires_ns"]):
                return None
            envelope = {
                "version": 1,
                "revision": str(row["revision"]),
                "kind": kind,
                "state": state,
            }
            encoded = canonical(envelope)
            if len(encoded.encode()) > self.settings.payload_bytes:
                envelope.pop("state")
                envelope["reference"] = {
                    "from_server": state["notification"]["from_server"],
                    "notification_id": str(row["id"]),
                }
                encoded = canonical(envelope)
            ttl = 86400
            if kind == "notification" and row["expires_ns"] is not None:
                ttl = min(ttl, max(0, int((int(row["expires_ns"]) - time.time_ns()) / 1e9)))
            return dict(sub), encoded, ttl

    def finish(
        self,
        job: dict,
        status: str,
        diagnostic: str,
        *,
        retry_after: str = "",
        disable: bool = False,
    ) -> None:
        with self.db.transaction() as con:
            if not con.execute(
                "SELECT 1 FROM jobs WHERE id=? AND status='processing' AND lease_token=?",
                (job["id"], job["lease_token"]),
            ).fetchone():
                return
            if disable:
                con.execute(
                    "UPDATE subscriptions SET status='disabled',challenge_hash=NULL,"
                    "challenge_expires=NULL WHERE installation=? AND version=?",
                    (job["installation"], job["version"]),
                )
            if status == "pending" and job["attempts"] >= self.settings.max_attempts:
                status, diagnostic = "failed", "attempts_exhausted"
            due = (
                time.time() + retry_delay(retry_after, job["attempts"])
                if status == "pending"
                else 0
            )
            con.execute(
                "UPDATE jobs SET status=?,diagnostic=?,due=?,lease_until=NULL,lease_token=NULL,"
                "payload=CASE WHEN ?='pending' THEN payload ELSE NULL END "
                "WHERE id=? AND status='processing' AND lease_token=?",
                (status, diagnostic, due, status, job["id"], job["lease_token"]),
            )
        logger.bind(event="push_result", job_id=job["id"], diagnostic=diagnostic).info("")

    def run_one(self) -> bool:
        job = self.claim()
        if job is None:
            return False
        try:
            prepared = self.prepare(job)
            if prepared is None:
                self.finish(job, "discarded", "stale_or_expired")
                return True
            sub, payload, ttl = prepared
            status, retry = self.transport.send(sub, payload, ttl)
            if 200 <= status <= 202:
                self.finish(job, "accepted", "push_service_accepted")
            elif status in (404, 410):
                self.finish(job, "failed", "subscription_gone", disable=True)
            elif status in (408, 425, 429) or 500 <= status <= 599:
                self.finish(job, "pending", "retryable_service_error", retry_after=retry)
            elif status in (401, 403):
                self.finish(job, "failed", "push_authentication_error")
            else:
                self.finish(job, "failed", "push_configuration_error")
        except Error:
            self.finish(job, "failed", "push_policy_or_configuration_error")
        except (OSError, TimeoutError, requests.RequestException):
            self.finish(job, "pending", "network_error")
        except Exception:
            # Do not log library exception messages: they may contain URL capabilities or bodies.
            self.finish(job, "failed", "internal_push_error")
        return True

    async def run(self) -> None:
        async def consume() -> None:
            while not self.stopping.is_set():
                worked = await asyncio.to_thread(self.run_one)
                if not worked:
                    try:
                        await asyncio.wait_for(self.stopping.wait(), timeout=0.5)
                    except TimeoutError:
                        pass

        await asyncio.gather(*(consume() for _ in range(self.settings.concurrency)))

    def stop(self) -> None:
        self.stopping.set()
