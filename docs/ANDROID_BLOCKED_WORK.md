# Android integration: blocked work

Last updated 2026-10-13 on server branch `phase-1-2-typed-api-client`. This document records what is blocked, the exact commands and results that were actually collected, and the commands that were deliberately NOT RUN with the reason. Nothing here claims a gate that was not executed.

## Repository state

| Item | Value |
|---|---|
| Server branch | `phase-1-2-typed-api-client` (up to date with origin) |
| Server HEAD | `c9a0d9b docs: add Phase 6 section to ANDROID_DEFERRED_TESTING` |
| Server submodule gitlink | `client` @ `4f66fc2` (client `chore: remove duplicate connector dependency`) |
| Client worktree branch | `phase-6-response-outbox` |
| Client worktree HEAD | `f7c0d70 fix: remove broken OutboxSyncWorkerTest` |
| Client merged into `origin/main`? | No. `git log --oneline origin/main..origin/phase-6-response-outbox` lists 13 commits; `git branch -a --contains f7c0d70` returns only `phase-6-response-outbox` (local + remote). |
| Client uncommitted edits | Phase 6 response/outbox, Phase 7 outbox/settings, and build configuration changes (dirty). |

The server gitlink (`4f66fc2`) is an ancestor of the client worktree HEAD, so the server records the client one phase behind the checked-out worktree. PRs #4 and #5 referenced in the earlier journal are **unmerged branch work**, not landed on `origin/main`.

## Evidence actually collected

```
cd client
git branch --show-current              # phase-6-response-outbox
git rev-parse HEAD                     # f7c0d7053e6df2b7ce816984c62a5c89466d2c30
git log --oneline origin/main..origin/phase-6-response-outbox   # 13 commits
./gradlew :app:testDebugUnitTest --offline --console=plain
    # BUILD SUCCESSFUL; XML totals: 129 tests, 0 failures, 0 errors, 0 skipped
    # ErrorClassifierTest = 25 tests; ContractConformanceTest = 12; EnvelopeParserTest = 13
./gradlew :app:assembleDebug --offline
    # BUILD SUCCESSFUL
grep -rn 'tok_prod_sec\|tok_stg_dev' app/src/main   # no matches
grep -rn '"Connected"' app/src/main                 # no matches
```

Toolchain at run time: `openjdk 25.0.4.1`, Gradle 9.3.1 wrapper, `local.properties` `sdk.dir=/usr/lib/android-sdk`.

## Phase 5 blockers

- **5.3 challenge lifecycle (device).** `UnifiedPushManager.handleRegistrationChallenge()` exists (`UnifiedPushManager.kt:82`) but the 409/410 re-registration and 404/422 terminal paths are unverified. Blocked: needs a device with a compatible UnifiedPush distributor to deliver a real challenge.
- **5.4 server-first DELETE (device).** Implemented in the unmerged worktree (`UnifiedPushManager.kt:45` calls `deleteSubscription(server)` before `UnifiedPush.unregister(...)` at `:47`). Blocked: ordering against a live server and a real distributor is unverified.
- **5.1 migration test on device.** `Migration_1_2_Test` (2 tests) passes on the JVM; the `androidTest` migration and Keystore instrumented tests do not exist yet (see Phase 8.3).

## Phase 6 blockers

- **6.4 remaining rule.** 410-with-receipt-as-success is implemented in `OutboxSyncEngine`; it is covered by `OutboxRetryTest`. Phase 6 client changes are committed locally but still require push/PR and submodule update.

## Phase 7 blockers

- **7.1 UX honesty.** Settings no longer presents the false `"Awaiting server integration"` status; full server onboarding state tests remain open.
- **7.4 release hygiene.** Demo tokens and unused Google/Firebase build gates are removed. Release assembly remains unverified because offline dependency cache lacks `androidx.concurrent:concurrent-futures-ktx:1.1.0`; application ID remains owner-reviewable.

## Phase 8 blockers

All Phase 8 gates are NOT RUN.

- **8.1 server regression gate.** Not run in this session.
- **8.2 Android unit gate.** The current JVM suite is green (129 tests). Full named-gate coverage and Compose UI tests remain incomplete; placeholders `ExampleUnitTest.kt` and `ExampleRobolectricTest.kt` remain.
- **8.3 instrumentation gate.** `app/src/androidTest/` holds only the placeholder `ExampleInstrumentedTest.kt`; `MigrationInstrumentedTest.kt` and `KeystoreInstrumentedTest.kt` do not exist.
- **8.4 release build gate.** Not run; `google-services` decision unresolved.
- **8.5 physical device and distributor matrix.** Needs a target GrapheneOS device and a compatible distributor.

## NOT RUN register

| Command | Reason |
|---|---|
| `hatch run check` (server, 61 pytest cases) | Not run this session; only Android docs were in scope. |
| `./gradlew :app:connectedDebugAndroidTest` | `No connected devices!`; no emulator or physical device attached. |
| `./gradlew :app:assembleRelease --offline` | Blocked by missing cached `androidx.concurrent:concurrent-futures-ktx:1.1.0`; no credentials were fabricated. |
| `aapt2 dump permissions` / DEX `tok_` scan | Depends on a release APK that was not built. |
| Operator deployment validation (`docker compose config`, live `/v1/server-info`, VAPID subject, trusted-proxy IPs) | No configured Notifyr deployment; local port 8080 serves an unrelated process. |
| Physical device matrix (register → challenge → deliver → respond → delete) | No device or distributor available. |
