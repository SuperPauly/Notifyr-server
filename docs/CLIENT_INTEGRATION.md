# Client integration and real delivery checks

The Android frontend `SuperPauly/Notifyr` was inspected at commit `d566bc7`.
Its delivery/response transports are currently demo implementations. Its
`UnifiedPushPayloadDecoder` expects a flat object and epoch-millisecond
timestamps. Server support is implemented here, but that frontend needs the
integration below to communicate with it. No frontend files were changed.

Use [API_CONTRACT.md](../API_CONTRACT.md) and `/openapi.json` as the contract:

- Register a HTTPS server connection by its discovered stable UUID; store its
  URL and recipient token in protected storage. Resolve `from_server` only
  through this registry. Verify discovery against the configured connection.
- Parse `notifications.v2`: keep decimal-string int64 IDs without floating-point
  conversion, UTC RFC3339 timestamps and optional text presence. `responded=false`
  is explicit. Android can convert timestamps for display after parsing.
- Decode the v1 push envelope, registration challenges, full state and reference
  fallback. Fetch references through the authenticated configured connection.
- Register each installation with its generated UUID, endpoint, RFC8291 p256dh
  key and auth secret. Obtain discovery's VAPID public key where the distributor
  or browser requires it. Plaintext-only UnifiedPush connectors cannot use this
  server: enable the connector's encryption support and forward its keys.
- Return the decrypted short-lived challenge through the authenticated confirm
  route with the exact installation/version. Rotate subscriptions after endpoint
  or key changes, and delete them on authorised unregistration.
- Store the highest revision per `(server, notification)` and ignore older or
  duplicate state. Reconcile the durable inbox on connection, resume and after
  errors; advance its cursor only after applying/persisting the returned page.
  A page cursor is separate from per-notification revisions.
- Display ordinary notifications and their actions. On a UnifiedPush response
  update, remove actions/update the existing notification without a fresh alert.
  Ignore expired initial alerts; disable actions on answered/expired inbox state.
- Create a response UUID once, retain it with the exact request in a durable
  outbox, and retry unchanged until its receipt or a terminal error is known.
  Buttons omit `text`; text replies include nonblank UTF-8 text up to 4096 bytes.
  Persist the receipt before removing outbox work. Handle already-answered,
  expired and conflicting requests explicitly.

Sending applications provision their own publisher token, generate request UUIDs
once and retry identical publications. Poll their response cursor without
consuming/deleting results; apply application effects idempotently by response
identity. Action IDs are data, not commands for the server to execute.

## Physical GrapheneOS / UnifiedPush check

Use a distributor and Android connector supporting encrypted Web Push as
described in the [official UnifiedPush introduction](https://unifiedpush.org/developers/intro/).
Choose a compatible distributor from the
[official distributor list](https://unifiedpush.org/users/distributors/), and
confirm the current connector documentation exposes endpoint, p256dh and auth.
Use a real HTTPS push origin explicitly allowed by this server. A self-hosted
private address needs the operator exception described in deployment docs.

1. On a physical GrapheneOS device install/start the distributor, grant the
   relevant notification/background permissions, and connect the integrated
   Notifyr app to a provisioned recipient. Record OS, app/connector/distributor
   versions, timestamp and server UUID without recording tokens or capability URLs.
2. Register an installation and verify it remains pending until the app receives
   and decrypts the challenge and confirms its exact version. Wrong user,
   version, expired and reused challenges must fail. Confirm active registration.
3. Publish an ordinary notification, button prompt and text-reply prompt using
   the contract examples. Check actual device display with app backgrounded and
   device locked, plus an oversized reference fetched with recipient auth.
4. Answer on one device; verify the receipt and publisher polling result. With a
   second installation for the same user, check actions are removed without a new
   alert. A separate recipient must still be able to answer independently.
5. Temporarily disconnect/restart the distributor/device, publish, reconnect and
   observe bounded retries plus authoritative inbox recovery. Rotate endpoint
   and keys; the old version must not activate or receive deliberately queued
   work. Unregister, and check invalid-service endpoints become disabled.
6. Test expiry and late/out-of-order delivery. Replay an identical accepted reply
   after expiry; it must return its original receipt, while a new reply fails.

Do not treat a 2xx push-service response or a successful API request as evidence
of device display. Record display separately from HTTP acceptance and receipt.

## Browser Web Push check

Use a secure-context web app and service worker. Request notification permission
after a user gesture and subscribe with `userVisibleOnly: true` and the decoded
server VAPID public key as `applicationServerKey`. Forward the subscription's
endpoint and `getKey('p256dh')` / `getKey('auth')` values as base64url keys through
the recipient-authenticated registration route. Never embed a recipient token in
a public bundle. Keep credentials in appropriately protected client storage.

In the service worker, decrypt/parse the delivered JSON through the browser Push
API and use `event.waitUntil(...)` to show the visible verification notification
required by the challenge envelope. Confirm the exact version via an
authenticated request; if protected auth is unavailable in the worker, keep the
registration pending and have the authenticated page complete confirmation
before the challenge expires. Test visible initial full/reference notifications,
click-through, endpoint/key rotation and deletion. While the page is active,
reconcile answered state using inbox cursors. Browser subscriptions do not get
silent response-state jobs, because userVisibleOnly delivery cannot depend on
silent updates. Use `showNotification` for every browser push that requires it.

Use two browsers/installs and two recipients to test ownership and isolation.
Inspect permission denial, closed/background browser delivery, expired alerts,
duplicate/reordered revisions and outbox retries. Check explicit CORS using the
actual browser origin through the HTTPS reverse proxy.

## Evidence boundary

Automated tests run here exercise ownership, encryption/VAPID through the real
pywebpush library, independent decryption, DNS/address policy at connection time,
durable worker retries/leases and restart recovery using a local test transport.
An isolated Docker smoke checks real HTTP and persistent storage. Those checks
do not prove delivery through a real distributor/browser to a physical device.
Physical GrapheneOS and browser delivery tests have **not run** in this workspace;
they require the integrated clients, push subscriptions and operator configuration
above. The live VPS/proxy has not been changed.
