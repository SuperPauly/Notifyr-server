# Android integration: deferred verification

Phase 1-4 and Phase 6 JVM unit tests and the debug build pass in the current worktree. Phase 5 is partial: the v2→v3 migration (5.1) and the connector registration client (5.2) exist, and the server-first DELETE (5.4) is now implemented; the challenge lifecycle (5.3) and everything requiring a distributor or a real device remain open. Do not treat a successful APK build as proof of distributor delivery or subscription activation.

1. **Finish Phase 5 lifecycle tests on device.** Phase 5.1 migration complete: `KeyMaterialStore` is sole persistence, plaintext Room columns dropped. Phase 5.4 server-first DELETE is implemented in the unmerged `phase-6-completion` worktree (`UnifiedPushManager.kt:45` calls `deleteSubscription(server)` before `UnifiedPush.unregister(...)` at `:47`), not yet device-verified. Remaining: prove installation UUID survives Room rebuild; key rotation issues a new PUT/version/challenge; challenge 409/410 retries registration and 404/422 stops; consumed or cross-installation challenges cannot activate; confirm the DELETE-before-unregister ordering against a live server.
2. **On a device with a compatible UnifiedPush distributor:** run `cd client && ./gradlew :app:connectedDebugAndroidTest`, register a real recipient, observe endpoint registration and challenge confirmation, then receive notification/reference/state-update payloads. Repeat after app restart, endpoint rotation, distributor outage, and unregister. Record redacted server responses and app status; never record tokens, endpoint URLs, auth secrets or challenge plaintext.
3. **Trust boundary:** inspect the merged manifest/APK and attempt a non-distributor broadcast against the connector receiver. Check that unknown/wrong registration tokens cannot route to a server. The connector is token-trusting, not sender-UID/signature authenticating; do not claim the latter without a different implementation.
4. **Operator environment:** `docker compose config --quiet` and isolated `hatch run smoke` pass locally, but `.env.example` values are placeholders and no local `.env`/Notifyr deployment is configured. The isolated smoke verifies container readiness/auth/persistence; local port 8080 belongs to an unrelated service (`/health` 200, `/ready` and `/v1/server-info` 404). Validate the routes, `NOTIFYR_PUSH_ORIGINS`, non-placeholder `NOTIFYR_VAPID_SUBJECT`, `NOTIFYR_HOST`, and restrictive trusted-proxy IPs against the actual deployment. Record redacted transcripts.
5. **Release device matrix:** after Phase 5 and operator checks pass, repeat registration, delivery, response and deletion on target GrapheneOS/distributor combinations; check release APK signing/configuration separately from debug.

Current environment (verified 2026-10-13): `cd client && ./gradlew :app:testDebugUnitTest --offline` GREEN (129 tests, 0 failures/errors/skipped); `./gradlew :app:assembleDebug --offline` GREEN; `./gradlew :app:assembleRelease --offline` produces `app-release-unsigned.apk`. Production signing requires configured keystore secrets. The server `hatch run check` (61 pytest cases) and isolated `hatch run smoke` were NOT re-run in this session. `:app:connectedDebugAndroidTest` cannot run (`No connected devices!`); operator and physical-device checks have not run against a configured Notifyr deployment.

## Phase 6: Response Wire Format & Delivery

Branch `phase-6-completion` @ `1b9b625` is **not merged** into `origin/main`. Client worktree is clean. Do not treat any of this as released.

### Implemented in the unmerged worktree
- [x] 6.2: ResponseSerializer wired into ApiResponseTransport; OutboxRepository sets isSimulated = false
- [x] 6.3: Receipt persistence wired (OutboxSyncWorker → OutboxDao.recordReceipt, which atomically sets status='SENT' plus the receipt columns); OutboxDao.clearSent refuses a SENT row without a receipt
- [x] 6.4: ErrorClassifier + retry logic (Retry-After parsed, network errors retryable, persisted attempt_count/next_attempt_at)
- [x] ErrorClassifierTest (25 tests GREEN, up from the earlier 12)

### Tests Deferred to Device/Integration Testing
- [x] ResponseWireFormatTest — 4096-byte UTF-8 limit, action_id validation
- [x] OutboxSyncWorkerTest — WorkManager enqueue coalescing and network constraint
- [x] ReceiptPersistenceTest — receipt written before SENT, clearSent() guard
- [x] OutboxRetryTest — Retry-After honored, terminal errors stop retrying, 410-with-receipt recovery

### Spec Deviations
- 410-with-receipt-as-success **is implemented** (`OutboxSyncEngine.kt:95`) and covered by `OutboxRetryTest`.
- The earlier "409 always already_responded" and "503 not distinguished" deviations are **resolved** in the committed `ErrorClassifier.kt` (which extracts the body error code and maps 409 idempotency_conflict and 503 not_ready).

### Test Infrastructure Requirements
The four Phase 6 tests now run with Robolectric, in-memory Room, and WorkManager test infrastructure. Device/distributor delivery, operator deployment, and release APK verification remain deferred.
