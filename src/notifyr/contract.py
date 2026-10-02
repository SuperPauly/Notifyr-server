"""Protobuf JSON conversion and documented HTTPS request/response schemas."""

import re
from typing import Literal

from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from google.protobuf.message import Message
from pydantic import BaseModel, ConfigDict, Field

from notifyr import notifications_pb2 as pb


class Error(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(code)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Action(Model):
    id: str
    label: str
    kind: Literal["BUTTON", "TEXT_REPLY"]


class Notification(Model):
    id: str = "0"
    title: str
    body: str = ""
    actions: list[Action] = Field(default_factory=list)
    expires_at: str | None = None
    open_path: str = ""
    created_at: str | None = None
    from_server: str = ""
    from_app: str = ""


class PublishRequest(Model):
    request_id: str
    recipient_user_ids: list[str]
    notification: Notification


class RespondRequest(Model):
    response_id: str
    notification_id: str
    action_id: str
    text: str | None = None
    from_server: str


class NotificationState(Model):
    notification: Notification
    responded: bool


class Receipt(Model):
    response_id: str
    accepted_at: str


class InboxItem(Model):
    revision: str
    state: NotificationState


class InboxPage(Model):
    items: list[InboxItem]
    cursor: str
    has_more: bool


class AppResponse(RespondRequest):
    responder_user_id: str
    from_app: str
    accepted_at: str
    revision: str


class ResponsePage(Model):
    items: list[AppResponse]
    cursor: str
    has_more: bool


class SubscriptionRequest(Model):
    delivery_type: Literal["unifiedpush", "webpush"]
    endpoint: str
    p256dh: str
    auth: str


class Confirmation(Model):
    version: int = Field(ge=1)
    challenge: str = Field(min_length=20, max_length=128)


class SubscriptionStatus(Model):
    installation_id: str
    version: int
    status: Literal["pending", "active", "disabled", "deleted"]


class ServerInfo(Model):
    server_id: str
    vapid_public_key: str
    contract: Literal["notifications.v2"] = "notifications.v2"


class Me(Model):
    scope: Literal["recipient", "publisher"]
    user_id: str | None = None
    app_id: str | None = None


class ErrorBody(Model):
    error: str


def json_message(message: Message) -> dict:
    return MessageToDict(
        message,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=True,
    )


def parse_message(payload: dict, message: Message) -> Message:
    try:
        return ParseDict(payload, message)
    except (ValueError, TypeError, ParseError) as exc:
        raise Error(422, "invalid_contract") from exc


def notification(payload: dict) -> pb.Notification:
    result = pb.Notification()
    parse_message(payload, result)
    return result


def positive_id(value: str, *, zero: bool = False) -> int:
    if not re.fullmatch(r"0|[1-9][0-9]{0,18}", value):
        raise Error(422, "invalid_id")
    number = int(value)
    if number > 2**63 - 1 or number < (0 if zero else 1):
        raise Error(422, "invalid_id")
    return number
