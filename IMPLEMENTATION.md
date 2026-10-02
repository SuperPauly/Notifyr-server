# Notifyr server implementation and acceptance record

Implement all six stages from the supplied prompt document. Preserve the existing
`graft/` directory. Prepare deployment files without deploying to the live VPS.

Required gates:

- Exact notifications.v2 contract and reproducible bindings; protobuf JSON semantics.
- Stable database identity and positive int64 allocation; hashed revocable tokens.
- Authorised publication, fan-out, idempotency, isolation, durable inbox cursors.
- Atomic per-recipient responses, optional text validation, expiry and retry semantics.
- Owned versioned subscriptions, persistent VAPID, authenticated encrypted challenges.
- Actual-connection SSRF enforcement and RFC8291 encryption using pywebpush.
- Durable leased delivery jobs, retries, restart recovery, stale/expired work handling.
- API/OpenAPI contract, examples, Hatch checks, non-root container and Compose assets.
- Automated integration and isolated container smoke tests; physical push tests reported separately.

Frontend examined: SuperPauly/Notifyr at d566bc7. Its transport is currently demo-only.
Its decoder expects flat notifications and epoch-millisecond timestamps. Required
client changes must be documented; do not weaken notifications.v2 or the specified
versioned envelope to match the demo decoder.

## Verification completed on 2026-10-01

`hatch run check` passed: locked protobuf generation, Ruff, MyPy (8 source files),
Bandit and **61 pytest cases**. Ruff formatting passed. All local pre-commit hooks
passed on the handwritten source, tests and scripts. `uv lock --check` passed
(71 locked packages). `docker compose config --quiet` passed.

The exact supplied proto was compared with the attachment's appendix: identical,
SHA-256 `ed6dc0e03cff0d736214b3215f14d904cc20d5db38cd5533c8d610452e579106`.
Regenerating both Python bindings and typing stubs returned exit 0 and produced
byte-identical files. The compiler is locked through grpcio-tools 1.78.0.

`hatch run smoke` passed using the final runtime code: Ubuntu 24.04 image,
Python 3.13.11, UID/GID 10001, read-only root filesystem, no network/host ports,
real readiness/authentication/publication/Get HTTP requests, graceful exit 0,
and identical UUID/VAPID public key with IDs 1 then 2 across container recreation.
Its disposable image, containers and volume were cleaned up. It never started
the repository's Compose stack or modified the live proxy.

The dependency stack emits a Starlette TestClient/httpx deprecation warning;
tests pass. Hatch's bootstrap environment also causes uv to warn that it uses
the project's `.venv` instead of Hatch's environment; the project lock is used
deliberately. Neither warning is a failed check or evidence of physical delivery.

## Requirement audit

| Stage | Implemented evidence | Verification evidence |
|---|---|---|
| 1: foundation and exact contract | `pyproject.toml`, `uv.lock`, exact `proto/notifications.proto`, reproducible `scripts/generate_proto.py`, generated bindings, `contract.py` JSON helpers/schemas; `db.py` WAL/FK/explicit transactions/hash-only revocable tokens/persistent UUID/int64 allocator/VAPID; `api.py` discovery/auth/health/readiness, Loguru sanitised sink, thread dispatch and one-worker launch | Exact appendix comparison and repeat compilation; startup/auth/revocation, protobuf presence and int64 >2^53 tests; restart/backup tests; corrupted identity fails closed; allocator exhaustion and year-9999 expiry tests; log redaction test; real container startup |
| 2: publication and inbox | `service.py` validates UUIDs, authorised deduplicated fan-out, source fields, actions, UTC expiry, UTF-8 limits and internal paths; one transaction persists notification/recipients/change snapshots/jobs and advances allocator; application-scoped semantic idempotency; recipient-only Get; durable revision paging | Fan-out/reorder/retry/isolation, spoofing, invalid path/action/UTF-8/expiry, request/stream/fan-out limits, concurrent identical publication, reply to an older notification during pagination; simulated queue failure proves publication and allocator rollback |
| 3: durable responses and app results | Recipient-derived responder; ownership before replay; optional-text rules, user-scoped UUID retry, distinct conflicts, per-recipient response uniqueness, expiry boundaries; response/receipt/inbox/jobs commit together; non-consuming application-scoped response cursor; one-response policy ponytail comment | Two concurrent devices accept one reply; concurrent accepted retries return one receipt; changed retries, after-expiry replay/new-response rejection, independent recipients, button absent text and 4096-byte reply boundaries, app isolation; queue failure rolls response back; push failure preserves accepted receipt/Get/publisher result |
| 4: owned verified subscriptions | `push.py` installation ownership/tombstones, unique endpoints, multiple devices, versioned rotation, validated P-256/auth keys; durable encrypted short-lived single-use version/user-bound challenges; visible browser challenge; persistent VAPID discovery; approved HTTPS origins and explicit private exceptions enforced on numeric socket connection with TLS hostname verification | Ownership/rotation/deletion/challenge/replay/expiry/key tests; blocked URLs and non-global/mixed DNS answers, mapped IPv6, private exceptions, numeric pinning/no second resolution, TLS context/SNI, no redirects/proxy/provider-body reads; bounded DNS helper test |
| 5: actual encrypted delivery | Real pywebpush RFC8291 aes128gcm and VAPID; lifecycle worker, four consumers, persistent atomically leased queue, unique lease ownership, bounded send/DNS timeouts and eight attempts; jitter/Retry-After, service/auth/config diagnostics, 404/410 disabling; recipient-state refresh/expiry/version checks, full or reference envelope; UnifiedPush response jobs, browser inbox reconciliation | Real library ciphertext independently decrypted with recipient key; VAPID header verified present; local transport retry/date/network/invalid subscription/exhaustion/restart recovery; stale lease cannot disable or overwrite; failure isolation; responded jobs strip actions, expired initial jobs discarded, response updates survive expiry, oversized fallback; lifespan challenge delivery and clean stop |
| 6: contract and reviewable deployment assets | `API_CONTRACT.md` all routes/scopes/models/codes/protobuf semantics/cursors/envelopes/challenges and runnable placeholder examples; accurate OpenAPI; Ubuntu Dockerfile/non-root/read-only Compose on existing external network, explicit proxy/CORS/origin env template; backup/restore and client/physical delivery guides; Hatch/pre-commit commands | OpenAPI route/model/security/CORS tests; ten documented curl examples replay against API, receipt retry matches; admin backup file mode/integrity/identity/auth verified; lock/compiler/checks/format/pre-commit/Compose validation and isolated Docker startup/persistence |

The code comments document the SQLite/single-process ceiling and the explicit
one-response-per-recipient simplification. No shared first-response-wins or
server execution of action IDs is implemented.

## Integration and deployment boundaries

The scope is the completed server repository. The supplied prompts explicitly
leave Android/web integration to clients using its contract. The reviewed
Android frontend still needs HTTPS transports, protected credentials, the v1
envelope and RFC3339/decimal-int64 parsing, subscription encryption keys and
challenge confirmation, revision/inbox reconciliation and a durable reply outbox.
See `docs/CLIENT_INTEGRATION.md` for specific work and physical test procedures.

No physical GrapheneOS/distributor or browser subscription delivery tests ran.
Automated encryption, transport and runtime evidence above does not prove actual
device display/read. Real delivery requires integrated clients, provisioned
tokens, push subscriptions and explicit operator origins. Deployment host/proxy/
CORS/contact placeholders must be filled and reviewed before activation. The
live VPS was not changed. See `docs/DEPLOYMENT.md` for deployment and consistent
backup/restore, including the allocator high-water requirement when recovering
an older snapshot.
