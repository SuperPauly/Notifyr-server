# Android integration: deferred verification

Phase 1–4 JVM tests and debug build pass. Phase 5 partial: migration v2→v3 removes plaintext key columns (5.1 complete); challenge lifecycle tests (5.3) and DELETE ordering (5.4) remain open. Do not treat a successful APK build as proof of distributor delivery or subscription activation.

1. **Finish Phase 5 lifecycle tests on device.** Phase 5.1 migration complete: `KeyMaterialStore` is sole persistence, plaintext Room columns dropped. Remaining: prove installation UUID survives Room rebuild; key rotation issues a new PUT/version/challenge; challenge 409/410 retries registration and 404/422 stops; consumed or cross-installation challenges cannot activate; **server DELETE precedes connector unregister** (currently unimplemented — `UnifiedPushManager.unregister()` calls connector but not server).
2. **On a device with a compatible UnifiedPush distributor:** run `cd client && ./gradlew :app:connectedDebugAndroidTest`, register a real recipient, observe endpoint registration and challenge confirmation, then receive notification/reference/state-update payloads. Repeat after app restart, endpoint rotation, distributor outage, and unregister. Record redacted server responses and app status; never record tokens, endpoint URLs, auth secrets or challenge plaintext.
3. **Trust boundary:** inspect the merged manifest/APK and attempt a non-distributor broadcast against the connector receiver. Check that unknown/wrong registration tokens cannot route to a server. The connector is token-trusting, not sender-UID/signature authenticating; do not claim the latter without a different implementation.
4. **Operator environment:** `docker compose config --quiet` and isolated `hatch run smoke` pass locally, but `.env.example` values are placeholders and no local `.env`/Notifyr deployment is configured. The isolated smoke verifies container readiness/auth/persistence; local port 8080 belongs to an unrelated service (`/health` 200, `/ready` and `/v1/server-info` 404). Validate the routes, `NOTIFYR_PUSH_ORIGINS`, non-placeholder `NOTIFYR_VAPID_SUBJECT`, `NOTIFYR_HOST`, and restrictive trusted-proxy IPs against the actual deployment. Record redacted transcripts.
5. **Release device matrix:** after Phase 5 and operator checks pass, repeat registration, delivery, response and deletion on target GrapheneOS/distributor combinations; check release APK signing/configuration separately from debug.

Current environment: `:app:compileDebugKotlin --offline`, `:app:testDebugUnitTest`, `:app:assembleDebug`, `docker compose config --quiet`, `hatch run check` (61 tests), and isolated `hatch run smoke` passed. `:app:connectedDebugAndroidTest` built but stopped with `No connected devices!`; operator and physical-device checks have not run against a configured Notifyr deployment.

## Phase 6: Response Wire Format & Delivery

### Completed (commit f7c0d70, PR #5)
- [x] 6.2: ResponseSerializer wired into ApiResponseTransport
- [x] 6.3: Receipt persistence wired (OutboxSyncWorker → OutboxDao.recordReceipt)
- [x] 6.4: ErrorClassifier + retry logic (Response passed for Retry-After, network errors retryable)
- [x] ErrorClassifierTest (12/12 tests GREEN)

### Tests Deferred to Device/Integration Testing
- [ ] ResponseWireFormatTest (deleted in 2b5d268) — 4096-byte UTF-8 limit, action_id validation
- [ ] OutboxSyncWorkerTest (removed in f7c0d70) — byte-identical response_id/payload on retry
- [ ] ReceiptPersistenceTest — receipt written before SENT, clearSent() guard, crash recovery
- [ ] OutboxRetryTest — Retry-After honored, terminal errors stop retrying

### Spec Deviations (Not Implemented)
- ErrorClassifier: 409 always "already_responded" (spec wants idempotency_conflict distinction)
- ErrorClassifier: 410-with-receipt-as-success not implemented
- ErrorClassifier: 503 not_ready not distinguished from other 5xx

### Test Infrastructure Requirements
All deferred tests require complex infrastructure beyond unit test scope:
- Robolectric for WorkManager/Room
- In-memory Room database
- WorkManager TestDriver
- Instrumented device for end-to-end verification
