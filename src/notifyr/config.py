"""Explicit operator settings. Secrets live in the private database, never here."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def csv(name: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in os.getenv(name, "").split(",") if value.strip())


@dataclass(frozen=True)
class Settings:
    database: Path = field(
        default_factory=lambda: Path(os.getenv("NOTIFYR_DB", "data/notifyr.sqlite"))
    )
    cors_origins: tuple[str, ...] = field(default_factory=lambda: csv("NOTIFYR_CORS_ORIGINS"))
    push_origins: tuple[str, ...] = field(default_factory=lambda: csv("NOTIFYR_PUSH_ORIGINS"))
    private_push_origins: tuple[str, ...] = field(
        default_factory=lambda: csv("NOTIFYR_PRIVATE_PUSH_ORIGINS")
    )
    vapid_subject: str = field(
        default_factory=lambda: os.getenv("NOTIFYR_VAPID_SUBJECT", "mailto:admin@example.invalid")
    )
    max_request_bytes: int = field(
        default_factory=lambda: int(os.getenv("NOTIFYR_MAX_REQUEST_BYTES", "131072"))
    )
    max_recipients: int = field(
        default_factory=lambda: int(os.getenv("NOTIFYR_MAX_RECIPIENTS", "100"))
    )
    worker_enabled: bool = field(
        default_factory=lambda: os.getenv("NOTIFYR_WORKER_ENABLED", "true").lower() == "true"
    )
    challenge_seconds: int = 300
    payload_bytes: int = 3000
    max_attempts: int = 8
    lease_seconds: int = 60
    concurrency: int = 4
    push_timeout: float = 10

    def __post_init__(self) -> None:
        if self.max_recipients < 1 or self.max_request_bytes < 1024:
            raise ValueError("Invalid request limits")
        if "*" in self.cors_origins:
            raise ValueError("Explicit browser CORS origins required")
        if not self.vapid_subject.startswith(("mailto:", "https://")):
            raise ValueError("VAPID subject must be a mailto: or HTTPS contact")
