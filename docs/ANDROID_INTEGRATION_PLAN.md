# Notifyr Android ⇄ Server Integration Plan

**Goal.** Make `Notifyr-server` deliver notifications to, and receive responses from, the Notifyr Android app (`SuperPauly/Notifyr` @ `d566bc7`) against the frozen `notifications.v2` HTTPS contract. No server contract change.

**Canonical path.** `client/` is the registered submodule @ `d566bc7`. `Notifyr/` is an untracked byte-identical clone — ignore it, never edit or delete it.

**TDD rule.** RED test → implementation → GREEN, in that order. No implementation lands before its test.

## Confirmed Facts

- Server contract frozen: 12 routes, v1 push envelope, RFC3339 timestamps, decimal-string int64 IDs, stable error codes (`API_CONTRACT.md:15-30,137-210,212-220`); 61 pytest cases pass (`tests/test_server.py`, `tests/test_push.py`; `IMPLEMENTATION.md:29-33`).
- All Android server paths demo/stubbed (`DemoNotificationTransport.kt:11,22,33`; `NotifyrApplication.kt:45`).
- Push decoder incompatible: flat fields, epoch-millis (`UnifiedPushPayloadDecoder.kt:36-95`).
- Inbox reconciliation is a no-op (`NotificationRepository.refreshNotifications()`); outbox shape correct but simulated (`OutboxRepository.kt:47-72`).
- Room v1, `exportSchema = false`, no migrations dir (`NotifyrDatabase.kt:14-20,33`).
- No `INTERNET` permission; `UnifiedPushReceiver` is `exported="true"` and unguarded (`AndroidManifest.xml:5-6,41-51`).
- `google-services` plugin with `WARN` and absent JSON; `firebase-appcheck` on the release classpath (`app/build.gradle.kts:7,73,113-114`).
- No UnifiedPush dependency exists — all broadcasts are hand-rolled; Keystore, Retrofit/OkHttp/Moshi, Room, WorkManager already available (`libs.versions.toml:15-17,22,33-35,43`).
- No JDK, no Android SDK, no `~/.gradle`; `ANDROID_HOME` empty.
- Server requires client-supplied `p256dh` (65-byte P-256) and `auth` (16-byte) and sends RFC8291 aes128gcm via pywebpush + VAPID (`API_CONTRACT.md:139-152,170-210`).

**Assumptions requiring validation.** A1/A2: a connector exists that accepts caller-supplied keys and decrypts RFC8291 to plaintext including the challenge — validated by Phase 0.3, which is a hard gate. A3: target device is GrapheneOS with a compatible distributor. A4: server needs no change. A5: toolchain is installable. A6: recipient bearer token is provisioned out-of-band.

**Dependency order.** `0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8`, with `0.3` blocking Phases 4 and 5. Critical path: `0.1 → 0.3 → 1.1 → 2.1 → 2.3 → 3.4 → 4.3 → 4.4 → 5.2 → 5.3 → 6.2 → 6.4 → 7.4 → 8.4 → 8.5`.

**Atomic commit strategy.** Two repos, independent histories. One concern per commit; test and implementation together or in two consecutive commits, never implementation first. The `client/` pointer bump is its own server commit, after client commits are pushed; never commit a dirty submodule. Schema commits (3.2, 3.4) are isolated for independent revert. Conventional Commits, subject ≤ 50 chars. Never commit `google-services.json`, keystores, `.env`, tokens, or capability URLs.

---

## Phase 0 — Foundations

- [ ] 0.1 Provision toolchain: install JDK 17+, Android SDK 36, warm Gradle 9.3.1, export `ANDROID_HOME`/`JAVA_HOME`
- [ ] 0.2 Lock canonical path: add `Notifyr/` to `.gitignore`; `diff -rq --exclude=.git client Notifyr` stays empty
- [ ] 0.3 **HARD GATE** connector verdict: pin a candidate in `client/gradle/libs.versions.toml`; write `client/app/src/test/java/com/example/spike/ConnectorCapabilityTest.kt` proving caller-supplied 65-byte P-256 and 16-byte auth. A negative verdict stops execution and forces a re-plan of Phases 4-5 around app-owned RFC8291
- [ ] 0.4 Operator readiness: `/health`, `/ready`, `/v1/server-info` return 200 (redacted transcripts); set `NOTIFYR_PUSH_ORIGINS`, a real `NOTIFYR_VAPID_SUBJECT`, `NOTIFYR_HOST`, `FORWARDED_ALLOW_IPS` (never `*`)
- [ ] **Verify 0:** `./gradlew :app:compileDebugKotlin` passes; `docker compose config --quiet` passes; the 0.3 verdict is written into the plan file

## Phase 1 — Connection Registry and Onboarding

- [x] 1.1 Add fields to `client/app/src/main/java/com/example/data/model/ServerEntity.kt`: `vapid_public_key`, `contract_version`, `identity_scope`/`identity_subject`, typed `connection_status`, `last_error_code`, `inbox_cursor`, `installation_id`, `subscription_version`/`subscription_status`, key columns, `connector_package` — write the field list with types, nullability, and defaults
- [x] 1.2 (RED→GREEN) `client/app/src/main/java/com/example/data/remote/ServerInfoClient.kt` — unauthenticated discovery that rejects a non-UUID `server_id`, a 64-byte or non-P-256 point, and a `contract` mismatch
- [x] 1.3 (RED→GREEN) `client/app/src/main/java/com/example/data/remote/MeClient.kt` — require `scope == recipient`, reject a publisher token, map 401 to `AUTH_REQUIRED`, and assert the bearer token never appears in any error string
- [x] 1.4 Remove the hardcoded `connectionStatus = "Connected"` (`ServerEntity.kt:44`) and the demo `testConnection` path (`client/app/src/main/java/com/example/data/repository/ServerRepository.kt:66-85`); states derive only from real probe results
- [ ] **Verify 1:** `grep -rn '"Connected"' client/app/src/main` returns no literal; `ServerInfoClientTest` and `MeClientTest` GREEN; `ConnectionStateTest` proves no path sets `Connected` without both probes succeeding

## Phase 2 — Typed API Client

- [x] 2.1 (RED→GREEN) models in `client/app/src/main/java/com/example/data/remote/model/` — decimal-string int64 IDs (never `Double`), RFC3339 via `Instant`, BUTTON `text` absent rather than null, explicit `responded:false`, `kind` ∈ notification|state_update|registration_challenge with mutually exclusive `state`|`reference`|challenge; prove `"9007199254740993"` round-trips losslessly and 9-digit fractional RFC3339 parses
- [x] 2.2 (RED) `client/app/src/test/java/com/example/data/remote/ContractConformanceTest.kt` — fixtures for every route in `API_CONTRACT.md:15-30` plus the full error table at `:212-220`; record the failing run before implementation
- [ ] 2.3 (GREEN) `client/app/src/main/java/com/example/data/remote/{NotifyrApi,ApiFactory}.kt` — Keystore-decrypted bearer per request, release strips the logging interceptor, no redirect following, typed 4xx versus retryable 5xx; add `retrofit`/`converter-moshi` from the existing `client/gradle/libs.versions.toml:22,33-35,74-87` with no new dependency
- [x] 2.4 Add `android.permission.INTERNET` to `client/app/src/main/AndroidManifest.xml:5-6` and `client/app/src/main/res/xml/network_security_config.xml` (system CA, no pinning, no cleartext); rewire `client/app/src/main/java/com/example/NotifyrApplication.kt:20,45,49-53` off `DemoNotificationTransport` and delete `simulateIncomingNotification()` from `NotificationRepository.kt`
- [x] **Verify 2:** `ContractConformanceTest` GREEN; `aapt2 dump permissions` on the APK lists INTERNET; `grep -rn "DemoNotificationTransport" client/app/src/main` returns zero; a header test shows the auth header present on protected routes and absent on `/v1/server-info`; no request log line contains the token

> **Phase 1+2 evidence note.** Static verification only — Gradle is not runnable in the sandbox (`gradlew` blocked by the lean-ctx allowlist), so the RED/GREEN runs of `ServerInfoClientTest`, `MeClientTest`, `ConnectionStateTest`, `WirePrimitivesTest`, `ContractConformanceTest`, and `AuthHeaderTest` are NOT yet executed; they must be run before Phase 3 starts. Verified statically: `grep -rn '"Connected"' client/app/src/main` → 0; `grep -rn "DemoNotificationTransport" client/app/src/main` → 0 (moved to `app/src/test/…/transport/DemoNotificationTransport.kt`); `INTERNET` + `android:networkSecurityConfig` present in `AndroidManifest.xml`; `http://` forbidden by `res/xml/network_security_config.xml`.
>
> **Phase 3 overlap flag.** `Migrations.kt` (`MIGRATION_1_2`) and the `NotifyrDatabase` version bump to 2 were written during Phase 1 already, but only cover the `servers` additions. Phase 3.1/3.2 must reconcile this: the migration still needs `notifications.revision` and the `outbox_responses` receipt/retry columns, plus `exportSchema = true` and `room.schemaLocation`. Phase 3 is where the migration is actually proven (RED→GREEN via `Migration_1_2_Test`); treat the Phase-1 migration as an unverified draft, not a done artifact.
>
> **Out-of-plan change.** `OutboxSyncWorker` referenced the removed `app.transport`; a real `ApiResponseTransport` (POST `/v1/responses`) was added and wired via `NotifyrApplication.responseTransport` to keep the module compiling. This is genuine Phase 6 delivery work and should be revisited (receipt handling, retry taxonomy) in Phase 6.

## Phase 3 — Room Schema and Migration

- [ ] 3.1 Write the v1→v2 schema diff with every column typed, defaulted, and nullability-marked: `servers` gains `vapid_public_key`, `contract_version`, `identity_scope`, `identity_subject`, `last_error_code`, `inbox_cursor`, `installation_id`, `subscription_version`, `subscription_status`, `subscription_p256dh`, `subscription_auth`, `connector_package`; `notifications` gains `revision`; `outbox_responses` gains `receipt_response_id`, `receipt_accepted_at`, `attempt_count`, `next_attempt_at`, `terminal_error_code` — state that v1 is valid today and this migration exists only for these additions
- [ ] 3.2 `client/app/src/main/java/com/example/data/local/Migrations.kt` — hand-written `Migration(1, 2)` (no `AutoMigration`: several columns are non-null with backfill semantics), keep `fallbackToDestructiveMigration(false)` in `client/app/src/main/java/com/example/data/local/NotifyrDatabase.kt:33`, set `exportSchema = true` and register `room.schemaLocation` in `client/app/build.gradle.kts`
- [ ] 3.3 (RED→GREEN) `client/app/src/test/java/com/example/data/local/Migration_1_2_Test.kt` — create a v1 database, insert representative rows including a mid-flight `SENDING` outbox row and an `is_simulated = true` row, run the migration, assert zero row loss and correct backfill; assert that opening a v1 database without the migration registered throws instead of wiping
- [ ] 3.4 Extend entities and DAOs in `client/app/src/main/java/com/example/data/{model,local}/` with `getPendingResponses(now)`, `recordReceipt`, `markTerminal`, `upsertCursor`, `highestRevision`, and a transactional `applyPageAndAdvanceCursor` that writes the page and advances the cursor in one unit
- [ ] **Verify 3:** `./gradlew :app:testDebugUnitTest --tests '*Migration*'` is GREEN; `client/schemas/com.example.data.local.NotifyrDatabase/2.json` is committed and regenerated by `./gradlew :app:kspDebugKotlin`; `grep -rn "fallbackToDestructiveMigration(true)" client/app/src` returns zero; `CursorDaoTest` proves the cursor is unchanged when a mid-page write throws

## Phase 4 — Envelope Parser and Inbox Sync

- [ ] 4.1 (RED) `client/app/src/test/java/com/example/data/unifiedpush/EnvelopeParserTest.kt` — cover all three `kind` values; reject `version: 2`, reject `state` and `reference` present together, compare revisions as decimal strings (`"9007199254740993"` > `"9007199254740992"`), accept 9-digit fractional RFC3339, **reject epoch-millis**, reject a flat body, reject a `from_server` that disagrees with the registered connection; record the failing run
- [ ] 4.2 (GREEN) rewrite `client/app/src/main/java/com/example/data/unifiedpush/UnifiedPushPayloadDecoder.kt` — require `version == 1`, dispatch on `kind`, enforce the `state`|`reference`|challenge XOR, parse RFC3339 and convert only at the storage boundary, bind `from_server` to the registry (preserve current line 51 behaviour), never navigate to a push-supplied URL, return `Ignored(reason)` instead of a silent `null`
- [ ] 4.3 `client/app/src/main/java/com/example/data/repository/InboxRepository.kt` — keep the per-notification highest `revision` (ignore anything ≤ stored) separate from the per-connection cursor; advance the cursor only after the page's writes commit, in one transaction; an empty page retains the caller's cursor; revision gaps are normal and must not error
- [ ] 4.4 Replace the no-op `refreshNotifications()` in `client/app/src/main/java/com/example/data/repository/NotificationRepository.kt` — page `GET /v1/inbox?cursor=&limit=50` while `has_more`, persist the returned cursor even when `has_more` is false, self-heal `invalid_cursor` 422 to `"0"` without looping
- [ ] 4.5 `client/app/src/main/java/com/example/data/unifiedpush/NotificationStateApplier.kt` — a `reference` resolves through authenticated `GET /v1/notifications/{id}?from_server=` on the registered connection and a fetch failure stays retryable, never swallowed; a `state_update` merges the existing alert, removes its actions, and raises no new notification
- [ ] 4.6 `client/app/src/main/java/com/example/data/worker/InboxSyncWorker.kt` — enqueue on startup, resume, network regained, and after a push; bounded backoff on 503 `not_ready`; safe to run alongside an in-flight push
- [ ] **Verify 4:** `EnvelopeParserTest`, `InboxPersistenceTest`, `InboxEngineTest`, `StateUpdateMergeTest`, and `InboxSyncWorkerTest` are all GREEN; `InboxPersistenceTest` proves older/equal revisions are ignored, the cursor is unchanged on a mid-page failure, and an empty page does not advance it; `InboxEngineTest` converges across three pages and self-heals a 422; `StateUpdateMergeTest` proves a `state_update` raises zero new notifications and an unknown id is ignored

## Phase 5 — UnifiedPush Registration and Challenge Lifecycle

- [ ] 5.1 `client/app/src/main/java/com/example/data/unifiedpush/KeyMaterialStore.kt` — persist the per-device installation UUID across restarts and DB rebuilds; generate the P-256 keypair with the private key held in `client/app/src/main/java/com/example/data/security/KeystoreManager.kt`; store the 16-byte auth secret encrypted; carry the app VAPID keypair and the `applicationServerKey` from `vapid_public_key`
- [ ] 5.2 (RED→GREEN) `client/app/src/main/java/com/example/data/remote/SubscriptionClient.kt` plus `UnifiedPushManager.kt` — `PUT /v1/subscriptions/{installation_id}` with the exact body (`delivery_type`, `endpoint`, `p256dh`, `auth`); take the endpoint from the distributor's `NEW_ENDPOINT` only; validate HTTPS and ≤2048 bytes client-side; base64url unpadded; store the returned integer `version`; the result is `pending`, never active
- [ ] 5.3 `client/app/src/main/java/com/example/data/unifiedpush/ChallengeHandler.kt` — decrypt the `registration_challenge` per the Phase 0.3 verdict, then confirm through `POST /v1/subscriptions/{id}/confirm` with the exact installation and version; 409 `challenge_not_current` and 410 `challenge_expired` trigger re-registration; 422 `invalid_challenge` and 404 are terminal; a challenge bound to another installation is refused; never request the plaintext challenge from the server; never retry a consumed challenge
- [ ] 5.4 Rotation and deletion — an endpoint or key change re-issues `PUT`, incrementing the version and requiring a fresh challenge so the old version never activates; authorised unregistration sends `DELETE` then the distributor `UNREGISTER` broadcast in that order, and server-profile deletion unregisters before clearing local state; 404 `subscription_not_found` and 403 `subscription_not_owned` are terminal
- [ ] 5.5 Guard the receiver in `client/app/src/main/AndroidManifest.xml:41-51` — add a signature-level permission or per-distributor sender verification to `UnifiedPushReceiver` (`android:exported="true"`, currently unguarded); keep `NotificationActionReceiver` non-exported at `:37-39`
- [ ] **Verify 5:** `KeyMaterialStoreTest` proves UUID stability, a 65-byte public key, a 16-byte auth secret, and a non-exportable private key; `SubscriptionClientTest` and `EndpointValidationTest` are GREEN; `ChallengeHandlerTest` proves `active` on success, correct re-registration on stale/expired, refusal for another installation, and no replay; `SubscriptionLifecycleTest` proves version bump and delete ordering; `aapt2 dump xmltree` shows the receiver guard and a non-distributor broadcast is rejected

## Phase 6 — Response Outbox, Receipts, and Retry Semantics

- [ ] 6.1 (RED) `client/app/src/test/java/com/example/data/response/ResponseWireFormatTest.kt` — a BUTTON response serialises with the `text` key absent entirely (not `null`, not `""`); a TEXT_REPLY carries non-blank text of at most 4096 **UTF-8 bytes**, so a 4097-byte reply and a 2000-character CJK reply are both rejected; `action_id` must exist in the notification's actions; record the failing run
- [ ] 6.2 (GREEN) real `POST /v1/responses` in `client/app/src/main/java/com/example/data/transport/NotificationResponseTransport.kt` and `client/app/src/main/java/com/example/data/worker/OutboxSyncWorker.kt:63-85` — reuse the `response_id` and payload already persisted in Room by `client/app/src/main/java/com/example/data/repository/OutboxRepository.kt:47-72` byte-identically on every retry; set `is_simulated = false` so no release build can emit a `SIMULATED` status
- [ ] 6.3 Persist `receipt_response_id` and `receipt_accepted_at` **before** marking a row delivered, and make `clearSent()` / `clearDelivered()` in `OutboxRepository.kt:101-107` refuse any `SENT` row that has no stored receipt; recovery relies on the server returning the original receipt for the same UUID after expiry
- [ ] 6.4 `client/app/src/main/java/com/example/data/remote/ErrorClassifier.kt` — retryable: network failure, 408, 425, 429, 5xx, and 503 `not_ready`, with exponential backoff plus jitter honouring a longer `Retry-After`; terminal: 410 `notification_expired`, 409 `already_responded`, 409 `idempotency_conflict`, 422 text/action codes, 401, 403; a 410 on a row that already holds a receipt is treated as **success**; replace the `runAttemptCount < 3` gate at `OutboxSyncWorker.kt:88` with the persisted `attempt_count` and `next_attempt_at`
- [ ] **Verify 6:** `ResponseWireFormatTest` and `ErrorClassifierTest` are GREEN; `OutboxSyncWorkerTest` proves a retry after a timeout sends a byte-identical `response_id` and payload; `ReceiptPersistenceTest` proves the receipt row is written before the status becomes `SENT`, that `clearSent()` refuses a `SENT` row without a receipt, and that a simulated crash between the HTTP 200 and the local write recovers on retry with the **same** `accepted_at`; `OutboxRetryTest` proves `Retry-After: 30` is honoured and no terminal error retries indefinitely

## Phase 7 — UX Honesty and Release Hygiene

- [ ] 7.1 `client/app/src/main/java/com/example/ui/servers/{ServersScreen,ServersViewModel}.kt` and `client/app/src/main/res/values/strings.xml` — replace demo onboarding with a real flow: enter the HTTPS base URL and the operator-provisioned bearer token, verify via `/v1/server-info` and `/v1/me`, then select a distributor; render honest states (`Verifying`, `Connected`, `AuthFailed`, `ServerMismatch`, `Unreachable`) with the server UUID and VAPID fingerprint; show subscription progress as Not Registered → Pending → Active, replacing the false `"Awaiting server integration"` literal in `client/app/src/main/java/com/example/data/unifiedpush/UnifiedPushConstants.kt`
- [ ] 7.2 `client/app/src/main/java/com/example/ui/{inbox,detail,components}/` — one card per `(from_server, id)`; a `state_update` merges in place with actions removed; action buttons disappear on answered or expired notifications; expired initial alerts are ignored; a replayed or out-of-order push is invisible to the user
- [ ] 7.3 `client/app/src/main/java/com/example/ui/outbox/{OutboxScreen,OutboxViewModel}.kt` and `client/app/src/main/java/com/example/data/model/OutboxStatus.kt` — truthful states (queued, sending, retrying with countdown, sent with receipt, failed terminal with code, auth-required); an honest offline state instead of a simulated success; manual retry for retryable failures; a confirmation prompt before deleting a queued unaccepted row, while accepted rows keep their receipt
- [ ] 7.4 Release hygiene in `client/app/build.gradle.kts:7,16,73,79,113-114` — remove the demo token fixtures `tok_prod_sec_9941a87e2b` and `tok_stg_dev_33890c21` from `client/app/src/main/java/com/example/data/transport/DemoNotificationTransport.kt:22,33`; resolve the `google-services` gate by supplying a real config or removing the plugin and `firebase-appcheck` from the release variant; review the generated-looking `applicationId`; strip the release logging interceptor
- [ ] **Verify 7:** `grep -rn "tok_.*_\(sec\|dev\)_" client/app/src/main` returns zero; `grep -rn '"Connected"' client/app/src/main` returns zero; Compose tests show `AuthFailed` rather than `Connected` when `/v1/me` fails, a pending subscription is never labelled active, a `state_update` yields one card with zero actions, a 429 renders a retry countdown, a 410 renders its terminal code, and deleting an unaccepted queued row prompts for confirmation; `./gradlew :app:assembleRelease` succeeds with a reviewed `google-services` decision

## Phase 8 — Release Gates and Physical Validation

- [ ] 8.1 Server regression gate — `hatch run check` in the server root (locked proto regeneration, Ruff, MyPy, Bandit, 61 pytest cases via `tests/test_server.py` and `tests/test_push.py`); `git diff --stat HEAD -- src tests proto` is empty, confirming no unplanned server change
- [ ] 8.2 Android unit gate — `./gradlew :app:testDebugUnitTest` in `client/` covering `EnvelopeParserTest`, `ContractConformanceTest`, `Migration_1_2_Test`, `CursorDaoTest`, `InboxPersistenceTest`, `InboxEngineTest`, `StateUpdateMergeTest`, `InboxSyncWorkerTest`, `KeyMaterialStoreTest`, `SubscriptionClientTest`, `ChallengeHandlerTest`, `SubscriptionLifecycleTest`, `ResponseWireFormatTest`, `ErrorClassifierTest`, `OutboxSyncWorkerTest`, `ReceiptPersistenceTest`, `OutboxRetryTest`, and the Compose tests; remove or replace the placeholders `client/app/src/test/java/com/example/{ExampleUnitTest,ExampleRobolectricTest}.kt`
- [ ] 8.3 Instrumentation gate — new `client/app/src/androidTest/java/com/example/data/local/MigrationInstrumentedTest.kt` and `client/app/src/androidTest/java/com/example/data/security/KeystoreInstrumentedTest.kt`; `./gradlew :app:connectedDebugAndroidTest` is GREEN on an API 24 emulator (minSdk) and on the target device, proving a real v1→v2 migration preserves every row and that the Keystore P-256 private key is non-exportable
- [ ] 8.4 Release build gate — `./gradlew :app:assembleRelease` in `client/`; `aapt2 dump permissions` lists `android.permission.INTERNET`; a DEX string scan finds no `tok_` literal; `grep -rn "CertificatePinner\|pin-set" client/app/src` returns zero; the `google-services` decision from 7.4 is recorded
- [ ] 8.5 Physical device and distributor matrix per `docs/CLIENT_INTEGRATION.md:60-97`, recording three **separate** verdicts per row — (a) distributor HTTP acceptance, (b) server receipt, (c) human-confirmed device display: register → decrypt challenge → `active`; publish ordinary, button, and text-reply notifications and confirm display with the app backgrounded and the device locked; resolve an oversized `reference` via authenticated fetch; answer on one installation and verify the receipt plus `GET /v1/apps/me/responses`; with a second installation for the same user confirm actions are removed with no new alert; confirm a second recipient answers independently; disconnect and restart the distributor, publish, reconnect, and observe bounded retries plus authoritative inbox recovery; rotate endpoint and keys and confirm the old version never activates; unregister and confirm invalid-service endpoints are disabled; test expiry and out-of-order delivery, with an identical accepted reply after expiry returning its original receipt while a new reply fails
- [ ] 8.6 Sign-off — record OS version, app/connector/distributor versions, timestamp, and server UUID for every physical row in `docs/ANDROID_INTEGRATION_PLAN.md`, marking each row PASS, FAIL, or **NOT RUN**; never record tokens or capability URLs
- [ ] **Verify 8:** `hatch run check` exits 0 with an empty server diff; `./gradlew :app:testDebugUnitTest` and `:app:connectedDebugAndroidTest` are GREEN at minSdk and target; `./gradlew :app:assembleRelease` exits 0 with INTERNET present, no `tok_` literal, and no pinning; every physical matrix row carries three separate verdicts or an explicit NOT RUN — a 2xx push-service response alone is not a pass

---

## Rollback and Data-Loss Safeguards

- Cursor advances only after page writes, in one transaction — never before
- Receipt persisted before any outbox cleanup; `clearSent()` refuses rows without one; recovery via same-UUID replay
- Response UUID persisted before first send and reused byte-identically; `idempotency_conflict` is terminal
- Mid-flight `SENDING` outbox row survives migration
- `fallbackToDestructiveMigration(false)` — missing migration throws, never wipes
- Rotation increments version, requires fresh challenge; old version never activates
- `from_server` bound to registry, never read from payload; references resolve only through authenticated Get on registered connection
- Terminal errors surfaced not retried; retryable back off honouring `Retry-After`

## Acceptance Criteria

- All 8 phase verification gates pass
- `hatch run check` exits 0 with no server diff
- Physical matrix: every row marked PASS, FAIL, or NOT RUN with three separate verdicts per row
- No demo tokens, no TLS pinning, no `fallbackToDestructiveMigration(true)`
- Release APK: INTERNET permission present, no `tok_` literals, `google-services` decision recorded

## Not Required / Out of Scope

- No public provisioning endpoint (`API_CONTRACT.md:13,22`) — operator provisions recipient token out-of-band
- No TLS pinning — contract mandates CA trust-store validation only; pinning breaks operator rotation
- Demo credentials are fixtures, not proven leaks — removal before release, not incident response
- No server contract change, no server-side action-ID execution, no browser Web Push work
- Server code is tested but deployment and device delivery are unverified (`IMPLEMENTATION.md:116-124`) — a 2xx is not a pass
