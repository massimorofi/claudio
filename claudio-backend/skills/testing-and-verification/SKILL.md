---
name: testing-and-verification
description: Summary of all skills for proving software actually works — end-to-end
  verification by driving the real flow, behavior testing, failing-test debugging,
  integration, smoke and pre-commit test runs, and snapshot generation. Use when asked
  to verify a change, run e2e/integration/smoke tests, write behavior tests, debug
  a failing test, generate or refresh snapshots, or run the pre-commit gate before
  shipping.
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-25'
  sources:
  - owner: built-in
    name: verify
    license: unknown
  - owner: super-* pack
    name: super-behavior-testing
    license: unknown
  - owner: super-* pack
    name: super-implementation-loop
    license: unknown
  - owner: MS Python ext
    name: debug-failing-test
    license: unknown
  - owner: MS Python ext
    name: run-e2e-tests
    license: unknown
  - owner: MS Python ext
    name: run-integration-tests
    license: unknown
  - owner: MS Python ext
    name: run-smoke-tests
    license: unknown
  - owner: MS Python ext
    name: run-pre-commit-checks
    license: unknown
  - owner: MS Python ext
    name: generate-snapshot
    license: unknown
---

# Testing & Verification

The unifying principle of every skill in this area: **a change is not done
until it has been exercised, not merely type-checked.**

## Constituent skills (9)

| Skill | Origin | What it contributes |
|---|---|---|
| verify | built-in | Verify a change by exercising it end-to-end (drive the flow, not just tests/typecheck) |
| super-behavior-testing | super-* pack | Specify and test observable behavior rather than internals |
| super-implementation-loop | super-* pack | Tight implement → test → correct loop |
| debug-failing-test | MS Python ext | Diagnose why a test fails |
| run-e2e-tests | MS Python ext | End-to-end test suite runner |
| run-integration-tests | MS Python ext | Integration test suite runner |
| run-smoke-tests | MS Python ext | Fast smoke checks |
| run-pre-commit-checks | MS Python ext | The full pre-commit gate |
| generate-snapshot | MS Python ext | Create/refresh snapshot baselines |

## Consolidated guidance

1. **Exercise, don't infer.** Type-checking and unit tests passing is not
   verification. Drive the actual user flow (`verify`) and observe the
   result.
2. **Test behavior, not implementation.** `super-behavior-testing`: assert
   on what the user/system does, so refactors don't break tests that were
   never about behavior.
3. **Layer the pyramid deliberately:** smoke (fast, "is it alive") → unit →
   integration (do the parts agree) → e2e (does the real flow work). Run
   the cheapest layer that can catch the class of bug you are worried about.
4. **A failing test is a hypothesis, not a verdict.** `debug-failing-test`:
   read the actual assertion and the actual value, isolate the smallest
   reproducer, and find the cause before changing anything.
5. **Never blind-accept a new snapshot.** `generate-snapshot`: a refreshed
   baseline that hides a regression is the worst possible outcome. Diff it
   and confirm the change is the intended one.
6. **The pre-commit gate is non-negotiable and must be fast.** If it is
   slow, people bypass it; if it is bypassable, it is not a gate.
7. **Loop tightly.** `super-implementation-loop`: small step → run →
   observe → correct. Long unverified stretches make failures ambiguous.
8. **Fix the test or the code, explicitly.** A test that is wrong is a
   defect. Say which one you are changing and why; never delete a test to
   get green.

## When to use this skill

"Verify this works", "run the e2e/integration/smoke tests", "write a
behavior test for…", "this test fails, why?", "refresh the snapshots",
"run pre-commit checks".

## Credits

This skill merges guidance from the following source skills, each the work of its respective owner and used under its original license. Preserve this attribution when reusing or re-merging.

| Owner | Skill | License |
|---|---|---|
| built-in | verify | unknown |
| super-* pack | super-behavior-testing | unknown |
| super-* pack | super-implementation-loop | unknown |
| MS Python ext | debug-failing-test | unknown |
| MS Python ext | run-e2e-tests | unknown |
| MS Python ext | run-integration-tests | unknown |
| MS Python ext | run-smoke-tests | unknown |
| MS Python ext | run-pre-commit-checks | unknown |
| MS Python ext | generate-snapshot | unknown |
