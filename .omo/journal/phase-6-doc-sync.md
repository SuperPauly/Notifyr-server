# Journal: Android integration doc sync

**Date:** 2026-10-13
**Branch:** `phase-6-completion` (server), client worktree `phase-6-completion` @ `66fdef9` (unmerged)

## What changed

- `docs/ANDROID_INTEGRATION_PLAN.md`: corrected the canonical-path header (server gitlink `66fdef9`, client worktree `66fdef9`, unmerged, dirty `OutboxRepository`/`OutboxSyncEngine`), refreshed the toolchain fact (JDK 25 + Gradle 9.3.1 + `sdk.dir` present), added a verified Status snapshot, and set Phase 2.3, 3.1-3.4, 4.1-4.6, 6.2, 6.3 to `[x]` with file:line evidence. Kept Phase 5.3/5.4, 6.1, 7.x, and all Phase 8 open with explicit NOT RUN reasons (6.4 was subsequently closed once the classifier and 410-with-receipt work landed). Fixed stale pointers (`ResponseWireFormatTest` absent; `"Awaiting server integration"` lives in `SettingsScreen.kt:128`, not `UnifiedPushConstants.kt`).
- `docs/ANDROID_DEFERRED_TESTING.md`: removed the stale "5.4 unimplemented" and "92/92" claims, corrected `ErrorClassifierTest` to 25, recorded that the branch is unmerged, and updated the spec-deviation list (409 `idempotency_conflict` / 503 `not_ready` resolved; 410-with-receipt-as-success implemented).
- `docs/ANDROID_BLOCKED_WORK.md`: new dedicated blocked-work document with repository state, collected evidence, per-phase blockers, and a NOT RUN register.
- `phase-5-6-completion.md` and `docs/ANDROID_INTEGRATION_PLAN.phase6.md`: marked superseded and pointed at the canonical docs (both untracked scratch).

## Commands run and results

```
cd client
git rev-parse HEAD                                      # 66fdef95dbf2dc6acf18eb97f6dd641c3bc9ef90
git rev-list --count origin/main..origin/phase-6-completion   # 8
git branch -a --contains 66fdef9                        # phase-6-completion (local + remote only)
./gradlew :app:testDebugUnitTest --offline --rerun      # BUILD SUCCESSFUL in 25s
    # XML: 129 tests, 0 failures, 0 errors, 0 skipped
    # ErrorClassifierTest 25, ContractConformanceTest 12, EnvelopeParserTest 13
./gradlew :app:assembleDebug --offline                  # BUILD SUCCESSFUL
grep -rn 'tok_prod_sec\|tok_stg_dev' app/src/main       # no matches
grep -rn '"Connected"' app/src/main                     # no matches
```

Server: `git rev-parse --short HEAD` = `fe832ac docs: record release verification`.

## NOT RUN (with reasons)

- Server `hatch run check` (61 pytest) and `hatch run smoke`: out of scope this session (Android docs only).
- `:app:connectedDebugAndroidTest`: `No connected devices!`.
- Signed release APK and physical APK inspection: unsigned `:app:assembleRelease --offline` output exists; production signing secrets and device tooling are unavailable.
- Physical device / distributor matrix, operator deployment validation: no device and no configured Notifyr deployment.

## Follow-ups

- The `ErrorClassifier.kt` / `ErrorClassifierTest.kt` refactor (409 `idempotency_conflict`, 503 `not_ready`, body error-code extraction) and 410-with-receipt-as-success are committed; remaining work is device/operator verification.
- `ResponseWireFormatTest`, `ReceiptPersistenceTest`, and `OutboxRetryTest` are committed; the device-only Phase 5/6 tests listed in `docs/ANDROID_BLOCKED_WORK.md` remain.
- Merge `phase-6-completion` into `origin/main`; the `client` submodule gitlink is already at `66fdef9`.
