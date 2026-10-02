# Notifyr HTTPS/JSON contract

Base URL: the operator-configured HTTPS server connection. Contract:
`notifications.v2`, exact source in `proto/notifications.proto`. No gRPC listener
is exposed. Publish, Get and Respond use the same protobuf conversion, validation
and transactional storage through these HTTPS routes. Interactive action IDs are
data; sending applications decide what accepted responses mean.

All errors have `{"error":"stable_code"}`. Validation errors never echo request
content or credentials. Bearer tokens are opaque, revocable, bound to a registered
user (`recipient`) or application (`publisher`). User/app/source identities are
derived from credentials. Application grants control recipients. There are no
public provisioning endpoints.

## Routes

| Method and route | Scope | Request | Successful response |
|---|---|---|---|
| GET /health | Public | None | 200 `{"status":"ok"}` |
| GET /ready | Public | None | 200 `{"status":"ready"}`; 503 when database unavailable or enabled worker has stopped |
| GET /v1/server-info | Public | None | `server_id`, `vapid_public_key`, `contract` |
| GET /v1/me | Either bearer scope | None | `scope` and `user_id` or `app_id` |
| POST /v1/notifications | Publisher | PublishRequest | 200 Notification after commit, including identical retries |
| GET /v1/notifications/{id}?from_server=UUID | Recipient | GetRequest via path/query | NotificationState |
| GET /v1/inbox?cursor=0&limit=50 | Recipient | Cursor/limit | `items:[{revision,state}]`, `cursor`, `has_more` |
| POST /v1/responses | Recipient | RespondRequest | 200 ResponseReceipt after commit |
| GET /v1/apps/me/responses?cursor=0&limit=50 | Publisher | Cursor/limit | `items:[AppResponse]`, `cursor`, `has_more` |
| PUT /v1/subscriptions/{installation_id} | Recipient | SubscriptionRequest | `installation_id`, integer `version`, `status:"pending"` |
| POST /v1/subscriptions/{installation_id}/confirm | Recipient | `version`, `challenge` | `installation_id`, `version`, `status:"active"` |
| DELETE /v1/subscriptions/{installation_id} | Recipient | None | 204 |
| GET /docs, /redoc, /openapi.json | Public | None | Generated API documentation/schema |

HTTPS is terminated at the existing reverse proxy. Bearer authentication is
required even behind the proxy. Unknown properties are rejected. IDs in JSON
requests and responses are canonical positive decimal **strings**, bounded to
signed int64; incoming Notification.id defaults to `"0"`. Enum kinds are
`"BUTTON"` or `"TEXT_REPLY"`. Timestamps use UTC RFC3339 on output, ending in `Z`,
with up to nine fractional digits. Protobuf JSON helpers preserve snake_case
field names. `responded:false` is always explicit. Omitted timestamps remain
absent. BUTTON text must be **absent**, not null or an empty string. TEXT_REPLY
text must be present, nonblank and at most 4096 UTF-8 bytes. Poll results omit
BUTTON text; they preserve TEXT_REPLY text exactly.

Notification output: `id`, `title`, `body`, ordered `actions`, optional
`expires_at`, `open_path`, `created_at`, `from_server`, `from_app`.
NotificationState: `notification` plus recipient-specific `responded`.
ResponseReceipt: `response_id`, `accepted_at`.
AppResponse: `response_id`, `notification_id`, `from_server`, `from_app`,
`action_id`, optional `text`, `responder_user_id`, `accepted_at`, `revision`.

## Validation, idempotency and cursor semantics

Publish requires a UUID `request_id`, existing authorised recipients, and a
Notification with zero ID and unset trusted source/creation fields. The original
recipient list is capped at `NOTIFYR_MAX_RECIPIENTS` (default 100), then sorted and
deduplicated. Publication stores one notification with independent recipient
records. Limits in UTF-8 bytes: title 256 (nonblank), body 16384, action ID/label
128 (nonblank), open_path 1024; at most eight actions with unique IDs and valid
kinds. Total request bytes are capped by `NOTIFYR_MAX_REQUEST_BYTES` (default
131072), including streamed bodies. Expiry must be a valid future timestamp.
`open_path` is internal relative navigation, optionally beginning with `/`;
external/protocol-relative URLs, schemes, backslashes, control characters,
traversal and ambiguous percent encoding are rejected.

Publish retry scope is `(application, request_id)`. Semantically identical
protobuf requests with deduplicated recipients return the original notification
even after expiry; changed payloads return 409. Current recipient grants are
checked before replaying a result. Invalid requests write no notification,
recipients, inbox changes or delivery jobs. Success follows one committed
transaction containing all these records.

Respond retry scope is `(recipient user, response_id)`. Server identity and
recipient ownership are checked before replay. Identical accepted retries return
the original receipt/timestamp after expiry. Changed requests under the same UUID
return `idempotency_conflict`; a different UUID after acceptance returns
`already_responded`. New responses at or after expiry return 410. Everyone in a
fan-out list can respond independently. Responses, inbox revisions and state
delivery intent are committed atomically; a later push failure cannot revoke
acceptance. Database uniqueness constraints and explicit write transactions
serialize simultaneous publication/reply attempts.

Inbox cursor is a durable append-only change revision, **not a notification ID**.
Every publication/response stores an immutable recipient-state snapshot. Answering
an older notification adds a newer revision. Pages order revisions ascending
inside a consistent read transaction; use returned cursor for the next page,
including when `has_more` is false. An empty page retains the caller's cursor.
Limits are 1..100 (default 50). Cursors are decimal strings; gaps caused by other
users are normal. Publisher response polling has its own append-only revision
stream and the same paging semantics. Neither stream is consumed or deleted by
polling. Persist cursors only after persisting the associated client state.

## Subscriptions and connection policy

Generate a persistent installation UUID on each device. Register with:

```json
{
  "delivery_type": "unifiedpush",
  "endpoint": "https://push.example.invalid/OPAQUE_CAPABILITY",
  "p256dh": "BASE64URL_UNCOMPRESSED_P256_PUBLIC_KEY",
  "auth": "BASE64URL_16_BYTE_AUTH_SECRET"
}
```

`delivery_type` is `unifiedpush` or `webpush`. Public key must decode to a valid
65-byte P-256 point; auth secret must decode to 16 bytes. Endpoints are HTTPS and
at most 2048 ASCII bytes. Several installations may belong to one user. An
endpoint can have one registration; duplicates return 409. Another user cannot
claim an installation, including a deleted installation. Replacement increments
the subscription version and requires a fresh challenge. Deletion removes
endpoint/key/challenge material and invalidates queued work while preserving
the ownership/version tombstone.

A registration remains pending. The server queues an encrypted
`registration_challenge` using the registered keys. The challenge is bound to
the user, installation and exact version, expires in five minutes and is single
use. The client returns `{ "version": 1, "challenge": "DECRYPTED_CHALLENGE" }`
through the authenticated confirmation route. The plaintext challenge is never
returned by registration. Browser challenge handlers must display the included
verification notification to honor `userVisibleOnly`. Confirming activates future
delivery; use the authoritative inbox to fetch notifications published before
confirmation.

The database persists the VAPID private/public pair with the server UUID. Public
key discovery is `/v1/server-info`; never regenerate a private key on restart.
Corrupt key material fails startup. Set a real VAPID contact in deployment.

Only exact operator-approved HTTPS origins in `NOTIFYR_PUSH_ORIGINS` or
`NOTIFYR_PRIVATE_PUSH_ORIGINS` are allowed. Private non-global destinations
require the latter explicit exception. Loopback, link-local, multicast and
unspecified addresses are always rejected. Every resolved A/AAAA answer must
pass, including IPv4-mapped IPv6. A bounded DNS resolver runs again inside the
actual connection path. The socket connects directly to a validated numeric IP;
TLS verifies the original hostname with SNI and the normal CA trust store.
No second hostname lookup, URL credentials, redirects or environment proxies
are used. Both registration prechecks and send-time enforcement apply. DNS
lookups are bounded to two seconds per record family; send-time socket/response
handling has a ten-second deadline.

## Push envelopes and delivery behavior

Versioned JSON envelopes are outside the unchanged protobuf contract. Initial
and recipient-state pushes contain:

```json
{
  "version": 1,
  "revision": "17",
  "kind": "notification",
  "state": {
    "notification": {
      "id": "9007199254740993",
      "title": "Deploy finished",
      "body": "Ready",
      "actions": [],
      "open_path": "",
      "created_at": "2026-10-01T12:00:00Z",
      "from_server": "SERVER_UUID",
      "from_app": "monitor"
    },
    "responded": false
  }
}
```

Kinds: `notification`, `state_update`, `registration_challenge`. Clients ignore
older/equal revisions **per (from_server, notification ID)**, reconcile through
the inbox and persist state before advancing cursors. A `state_update` removes
actions and reconciles an existing alert without a fresh alert. After responding,
only that user's UnifiedPush installations get state-update jobs. Browsers use
the inbox when active; no silent state pushes are sent to userVisibleOnly browser
subscriptions. Other recipients remain able to answer.

The UTF-8 plaintext payload budget is 3000 bytes, leaving ample space under a
4096-byte encrypted Web Push body limit for RFC8291 aes128gcm framing. Larger
notifications use the same version/revision/kind fields with a `reference`
instead of `state`:

```json
{"version":1,"revision":"17","kind":"notification","reference":{"from_server":"SERVER_UUID","notification_id":"42"}}
```

Resolve references through the registered server connection and authenticated
Get; never navigate to a URL from the push. For browser initial references,
display a generic visible notification if fetching cannot complete immediately,
then reconcile; do not silently swallow the push. A challenge includes
`installation_id`, integer `subscription_version`, `challenge`, `from_server`
and `visible_notification:{title,body}` instead of state/revision.

The embedded lifespan worker uses durable jobs, four consumers, atomic leases,
eight maximum attempts and a 60-second lease. Restarted workers reclaim expired
leases. Network failures, HTTP 408/425/429 and 5xx retry with exponential delay
capped at one hour plus jitter, honoring longer Retry-After seconds/dates.
404/410 disable the exact subscription version. 401/403, policy violations,
redirects and other configuration failures stop the job with a distinct sanitised
diagnostic. `hatch run admin push-status` lists status/counts without endpoint
capabilities or message bodies.

Every send checks current subscription version, recipient state and expiry.
Expired unanswered initial jobs are discarded. Stale UnifiedPush initial work
refreshes to answered state; stale browser work is discarded. Accepted response
updates remain valid after expiry. A response can still race with a send already
in progress, and push services can delay delivery: clients must check revisions
and sync the authoritative inbox. `accepted` means push-service acceptance,
not displayed/read. Delivery is best effort with bounded attempts, and can
duplicate after a crash; neither delivery nor exactly-once push is guaranteed.

## Stable error codes

| HTTP | Codes |
|---|---|
| 401 | unauthorized (includes WWW-Authenticate: Bearer) |
| 403 | scope_required, recipient_not_authorized, subscription_not_owned |
| 404 | notification_not_found, subscription_not_found (also used for another user's records) |
| 409 | server_mismatch, idempotency_conflict, already_responded, endpoint_already_registered, challenge_not_current |
| 410 | notification_expired, challenge_expired |
| 413 | request_too_large |
| 422 | invalid_request, invalid_contract, invalid_uuid, invalid_id, source_spoofing, invalid_recipients, fanout_limit, invalid_content, invalid_open_path, action_limit, invalid_actions, invalid_expiry, invalid_text, unexpected_text, text_required, unknown_action, invalid_cursor, invalid_limit, invalid_push_endpoint, push_origin_not_allowed, push_destination_blocked, invalid_push_keys, invalid_challenge |
| 503 | not_ready, id_space_exhausted, push_destination_unavailable |
| 500 | internal_error |

Framework routing errors (e.g. unknown route/method) retain FastAPI's standard
404/405 shape. Worker diagnostics are local admin data, not API error responses.

## Copyable examples

Replace placeholders with your server, UUIDs and provisioned credentials. Each
new publication uses a new request UUID; save it for retries.

```bash
BASE='https://notifyr.example.invalid'
PUBLISHER_TOKEN='REPLACE_PUBLISHER_TOKEN'
RECIPIENT_TOKEN='REPLACE_RECIPIENT_TOKEN'

# Ordinary notification
curl --fail-with-body "$BASE/v1/notifications" \
  -H "Authorization: Bearer $PUBLISHER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"request_id":"00000000-0000-4000-8000-000000000001","recipient_user_ids":["alice"],"notification":{"title":"Job completed","body":"Your report is ready"}}'

# Button notification
curl --fail-with-body "$BASE/v1/notifications" \
  -H "Authorization: Bearer $PUBLISHER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"request_id":"00000000-0000-4000-8000-000000000002","recipient_user_ids":["alice"],"notification":{"title":"Approve deployment?","actions":[{"id":"approve","label":"Approve","kind":"BUTTON"}]}}'

# Text-reply notification
curl --fail-with-body "$BASE/v1/notifications" \
  -H "Authorization: Bearer $PUBLISHER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"request_id":"00000000-0000-4000-8000-000000000003","recipient_user_ids":["alice"],"notification":{"title":"Leave a comment","actions":[{"id":"comment","label":"Reply","kind":"TEXT_REPLY"}]}}'

# Several recipients; repeated alice is deduplicated
curl --fail-with-body "$BASE/v1/notifications" \
  -H "Authorization: Bearer $PUBLISHER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"request_id":"00000000-0000-4000-8000-000000000004","recipient_user_ids":["alice","bob","alice"],"notification":{"title":"Team update"}}'

# Discover the stable server UUID, then use the actual returned notification ID.
curl --fail-with-body "$BASE/v1/server-info"
SERVER_ID='REPLACE_SERVER_UUID'
NOTIFICATION_ID='REPLACE_BUTTON_NOTIFICATION_DECIMAL_ID'

# Accepted button response: text is absent
curl --fail-with-body "$BASE/v1/responses" \
  -H "Authorization: Bearer $RECIPIENT_TOKEN" -H 'Content-Type: application/json' \
  -d "{\"response_id\":\"00000000-0000-4000-8000-000000000005\",\"notification_id\":\"$NOTIFICATION_ID\",\"from_server\":\"$SERVER_ID\",\"action_id\":\"approve\"}"

# Idempotent retry: exact response UUID and payload, same receipt/accepted_at
curl --fail-with-body "$BASE/v1/responses" \
  -H "Authorization: Bearer $RECIPIENT_TOKEN" -H 'Content-Type: application/json' \
  -d "{\"response_id\":\"00000000-0000-4000-8000-000000000005\",\"notification_id\":\"$NOTIFICATION_ID\",\"from_server\":\"$SERVER_ID\",\"action_id\":\"approve\"}"

# Publisher responses and recipient inbox
curl --fail-with-body "$BASE/v1/apps/me/responses?cursor=0&limit=50" \
  -H "Authorization: Bearer $PUBLISHER_TOKEN"
curl --fail-with-body "$BASE/v1/inbox?cursor=0&limit=50" \
  -H "Authorization: Bearer $RECIPIENT_TOKEN"
curl --fail-with-body "$BASE/v1/notifications/$NOTIFICATION_ID?from_server=$SERVER_ID" \
  -H "Authorization: Bearer $RECIPIENT_TOKEN"
```

Protocol sources: [Protobuf JSON](https://protobuf.dev/programming-guides/json/),
[UnifiedPush application server integration](https://unifiedpush.org/developers/intro/),
[pywebpush](https://github.com/web-push-libs/pywebpush),
[RFC8030](https://www.rfc-editor.org/rfc/rfc8030),
[RFC8291](https://www.rfc-editor.org/rfc/rfc8291),
[Push API userVisibleOnly](https://www.w3.org/TR/push-api/).
