import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PublishRequest(_message.Message):
    __slots__ = ("request_id", "recipient_user_ids", "notification")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    RECIPIENT_USER_IDS_FIELD_NUMBER: _ClassVar[int]
    NOTIFICATION_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    recipient_user_ids: _containers.RepeatedScalarFieldContainer[str]
    notification: Notification
    def __init__(self, request_id: _Optional[str] = ..., recipient_user_ids: _Optional[_Iterable[str]] = ..., notification: _Optional[_Union[Notification, _Mapping]] = ...) -> None: ...

class Notification(_message.Message):
    __slots__ = ("id", "title", "body", "actions", "expires_at", "open_path", "created_at", "from_server", "from_app")
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    BODY_FIELD_NUMBER: _ClassVar[int]
    ACTIONS_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    OPEN_PATH_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    FROM_SERVER_FIELD_NUMBER: _ClassVar[int]
    FROM_APP_FIELD_NUMBER: _ClassVar[int]
    id: int
    title: str
    body: str
    actions: _containers.RepeatedCompositeFieldContainer[Action]
    expires_at: _timestamp_pb2.Timestamp
    open_path: str
    created_at: _timestamp_pb2.Timestamp
    from_server: str
    from_app: str
    def __init__(self, id: _Optional[int] = ..., title: _Optional[str] = ..., body: _Optional[str] = ..., actions: _Optional[_Iterable[_Union[Action, _Mapping]]] = ..., expires_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., open_path: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., from_server: _Optional[str] = ..., from_app: _Optional[str] = ...) -> None: ...

class Action(_message.Message):
    __slots__ = ("id", "label", "kind")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        KIND_UNSPECIFIED: _ClassVar[Action.Kind]
        BUTTON: _ClassVar[Action.Kind]
        TEXT_REPLY: _ClassVar[Action.Kind]
    KIND_UNSPECIFIED: Action.Kind
    BUTTON: Action.Kind
    TEXT_REPLY: Action.Kind
    ID_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    id: str
    label: str
    kind: Action.Kind
    def __init__(self, id: _Optional[str] = ..., label: _Optional[str] = ..., kind: _Optional[_Union[Action.Kind, str]] = ...) -> None: ...

class GetRequest(_message.Message):
    __slots__ = ("notification_id", "from_server")
    NOTIFICATION_ID_FIELD_NUMBER: _ClassVar[int]
    FROM_SERVER_FIELD_NUMBER: _ClassVar[int]
    notification_id: int
    from_server: str
    def __init__(self, notification_id: _Optional[int] = ..., from_server: _Optional[str] = ...) -> None: ...

class NotificationState(_message.Message):
    __slots__ = ("notification", "responded")
    NOTIFICATION_FIELD_NUMBER: _ClassVar[int]
    RESPONDED_FIELD_NUMBER: _ClassVar[int]
    notification: Notification
    responded: bool
    def __init__(self, notification: _Optional[_Union[Notification, _Mapping]] = ..., responded: bool = ...) -> None: ...

class RespondRequest(_message.Message):
    __slots__ = ("response_id", "notification_id", "action_id", "text", "from_server")
    RESPONSE_ID_FIELD_NUMBER: _ClassVar[int]
    NOTIFICATION_ID_FIELD_NUMBER: _ClassVar[int]
    ACTION_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    FROM_SERVER_FIELD_NUMBER: _ClassVar[int]
    response_id: str
    notification_id: int
    action_id: str
    text: str
    from_server: str
    def __init__(self, response_id: _Optional[str] = ..., notification_id: _Optional[int] = ..., action_id: _Optional[str] = ..., text: _Optional[str] = ..., from_server: _Optional[str] = ...) -> None: ...

class ResponseReceipt(_message.Message):
    __slots__ = ("response_id", "accepted_at")
    RESPONSE_ID_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_AT_FIELD_NUMBER: _ClassVar[int]
    response_id: str
    accepted_at: _timestamp_pb2.Timestamp
    def __init__(self, response_id: _Optional[str] = ..., accepted_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...
