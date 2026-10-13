# Journal: Android integration doc sync

**Date:** 2026-10-13
**Branch:** `phase-1-2-typed-api-client` (server), client worktree `phase-6-response-outbox` @ `f7c0d70` (unmerged)

## What changed

- `docs/ANDROID_INTEGRATION_PLAN.md`: corrected the canonical-path header (server gitlink `4f66fc2`, client worktree `f7c0d70`, unmerged, dirty `ErrorClassifier`), refreshed the toolchain fact (JDK 25 + Gradle 9.3.1 + `sdk.dir` present), added a verified Status snapshot, and set Phase 2.3, 3.1-3.4, 4.1-4.6, 6.2, 6.3 to `[x]` with file:line evidence. Kept Phase 5.3/5.4, 6.1, 6.4, 7.x, and all Phase 8 open with explicit NOT RUN reasons. Fixed stale pointers (`ResponseWireFormatTest` absent; `"Awaiting server integration"` lives in `SettingsScreen.kt:128`, not `UnifiedPushConstants.kt`).
- `docs/ANDROID_DEFERRED_TESTING.md`: removed the stale "5.4 unimplemented" and "92/92" claims, corrected `ErrorClassifierTest` to 25, recorded that the branch is unmerged and the classifier edits are uncommitted, and updated the spec-deviation list (409 `idempotency_conflict` / 503 `not_ready` are resolved only in the uncommitted worktree; 410-with-receipt-as-success still missing).
- `docs/ANDROID_BLOCKED_WORK.md`: new dedicated blocked-work document with repository state, collected evidence, per-phase blockers, and a NOT RUN register.
- `phase-5-6-completion.md` and `docs/ANDROID_INTEGRATION_PLAN.phase6.md`: marked superseded and pointed at the canonical docs (both untracked scratch).

## Commands run and results

```
cd client
git rev-parse HEAD                                      # f7c0d7053e6df2b7ce816984c62a5c89466d2c30
git rev-list --count origin/main..origin/phase-6-response-outbox   # 13
git branch -a --contains f7c0d70                        # phase-6-response-outbox (local + remote only)
./gradlew :app:testDebugUnitTest --offline --rerun      # BUILD SUCCESSFUL in 25s
    # XML: 112 tests, 0 failures, 0 errors, 0 skipped
    # ErrorClassifierTest 25, ContractConformanceTest 12, EnvelopeParserTest 13
./gradlew :app:assembleDebug --offline                  # BUILD SUCCESSFUL
grep -rn 'tok_prod_sec\|tok_stg_dev' app/src/main       # no matches
grep -rn '"Connected"' app/src/main                     # no matches
```

Server: `git rev-parse --short HEAD` = `c9a0d9b docs: add Phase 6 section to ANDROID_DEFERRED_TESTING`.

## NOT RUN (with reasons)

- Server `hatch run check` (61 pytest) and `hatch run smoke`: out of scope this session (Android docs only).
- `:app:connectedDebugAndroidTest`: `No connected devices!`.
- `:app:assembleRelease`, `aapt2 dump permissions`, DEX `tok_` scan: release build not run; `google-services` decision unresolved.
- Physical device / distributor matrix, operator deployment validation: no device and no configured Notifyr deployment.

## Follow-ups

- Commit the uncommitted `ErrorClassifier.kt` / `ErrorClassifierTest.kt` (409 `idempotency_conflict`, 503 `not_ready`, body error-code extraction) and implement 410-with-receipt-as-success.
- Restore `ResponseWireFormatTest`, `ReceiptPersistenceTest`, `OutboxRetryTest`, and the Phase 5/6 tests listed in `docs/ANDROID_BLOCKED_WORK.md`.
- Merge `phase-6-response-outbox` and advance the `client` submodule gitlink from `4f66fc2`.
