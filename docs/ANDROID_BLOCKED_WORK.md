# Android integration: blocked work

Last updated 2026-10-14 on server branch `phase-6-completion`. This document records what is blocked, the exact commands and results that were actually collected, and the commands that were deliberately NOT RUN with the reason. Nothing here claims a gate that was not executed.

## Repository state

| Item | Value |
|---|---|
| Server branch | `phase-6-completion` (up to date with origin) |
| Server HEAD | `cb0c982 chore: advance client integration pointer` |
| Server submodule gitlink | `client` @ `1b9b625` (client `ci: require release signing secrets`) |
| Client worktree branch | `phase-6-completion` |
| Client worktree HEAD | `1b9b625 ci: require release signing secrets` |
| Client merged into `origin/main`? | No. `phase-6-completion` is pushed and remains open for review. |
| Client uncommitted edits | None. |

The server gitlink (`1b9b625`) matches the client worktree HEAD, so the server records the checked-out client state. PR #6 remains open and is not landed on `origin/main`.

## Evidence actually collected

```
cd client
git branch --show-current              # phase-6-completion
git rev-parse HEAD                     # 66fdef95dbf2dc6acf18eb97f6dd641c3bc9ef90
git log --oneline origin/main..origin/phase-6-completion   # 8 commits
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

- **6.4 remaining rule.** 410-with-receipt-as-success is implemented in `OutboxSyncEngine`; it is covered by `OutboxRetryTest`. Phase 6 client changes are committed and pushed to `origin/phase-6-completion`, and the server gitlink is updated to `66fdef9`. Remaining: merge into `origin/main`.

## Phase 7 blockers

- **7.1 UX honesty.** Settings no longer presents the false `"Awaiting server integration"` status; full server onboarding state tests remain open.
- **7.4 release hygiene.** Demo tokens and unused Google/Firebase build gates are removed. Unsigned release assembly succeeds; production signing requires `KEYSTORE_PATH`, `STORE_PASSWORD`, and `KEY_PASSWORD`. Application ID remains owner-reviewable.

## Phase 8 blockers

All Phase 8 gates are NOT RUN.

- **8.1 server regression gate.** Not run in this session.
- **8.2 Android unit gate.** The current JVM suite is green (129 tests). Full named-gate coverage and Compose UI tests remain incomplete; placeholders `ExampleUnitTest.kt` and `ExampleRobolectricTest.kt` remain.
- **8.3 instrumentation gate.** `app/src/androidTest/` holds only the placeholder `ExampleInstrumentedTest.kt`; `MigrationInstrumentedTest.kt` and `KeystoreInstrumentedTest.kt` do not exist.
- **8.4 release build gate.** Unsigned `assembleRelease` succeeds; the permission/DEX-string scan and the `google-services` decision record remain open.
- **8.5 physical device and distributor matrix.** Needs a target GrapheneOS device and a compatible distributor.

## NOT RUN register

| Command | Reason |
|---|---|
| `hatch run check` (server, 61 pytest cases) | Not run this session; only Android docs were in scope. |
| `./gradlew :app:connectedDebugAndroidTest` | `No connected devices!`; no emulator or physical device attached. |
| `./gradlew :app:assembleRelease --offline` | Produces `app-release-unsigned.apk`; signed production APK requires configured keystore secrets. |
| `aapt2 dump permissions` / DEX `tok_` scan | Not run; the unsigned release APK exists but the permission/DEX scan was not performed. |
| Operator deployment validation (`docker compose config`, live `/v1/server-info`, VAPID subject, trusted-proxy IPs) | No configured Notifyr deployment; local port 8080 serves an unrelated process. |
| Physical device matrix (register → challenge → deliver → respond → delete) | No device or distributor available. |
