# optio-core test race hardening — Seed Specification

**Base revision:** `e1dd45318723a019a41be0317f8dd00515fd64ce` on branch `main` (as of 2026-06-06T18:08:58Z)

**Date:** 2026-06-06
**Status:** Seed

**Purpose of this document:** capture the remaining work after the first pass of
de-flaking optio-core's async tests, so a later session can pick it up without
re-deriving the analysis. The clean-cut fixes already landed (see "Already done");
what remains is one genuinely tricky test plus a handful of low-confidence
candidates, plus a systemic guard to prevent regressions.

---

## Background — the antipattern

Many optio-core tests stage a *sequence of concurrent events* (launch task,
spawn child, enter shutdown, register a guard, take a snapshot) and then assert
against a state that only holds once the first event has reached a specific
point. Several of them established that precondition with a **fixed
`asyncio.sleep(<small>)`** — a guess that "by now the other thing has happened."
Under scheduling jitter or machine/CI load the guess is wrong: the dependent
action runs too early or too late and the test flakes. The original report was
`test_no_block_new_launches_post_snapshot_not_cancelled` flaking ~45% locally.

**The rule:** gate the next event on a *completion signal* from the previous
step — an `asyncio.Event` set at the real point, or a bounded poll on the actual
state — never on a fixed sleep.

**The exception:** if a test's *purpose is to exercise the race itself*
(e.g. interleaving cancel with natural finalization), do NOT gate the race away.
Make the *setup* deterministic and leave the contended step racing, or force a
specific interleave via injection. None of the work below is a race test in this
sense — but the next person must keep the distinction in mind.

## Already done (landed on this branch / main)

Replaced fixed-sleep ordering gates with completion signals in 13 tests across
5 files; each verified 10/10 rounds under load:

- `test_group_cancel.py`: `test_no_block_new_launches_post_snapshot_not_cancelled`
  (wrap `list_processes` → snapshot-complete event; also gate the survivor read
  on `started_b` so it asserts `running`, not the racy `scheduled`),
  `test_block_new_launches_rejects_during_call` (poll `_launch_blocks`),
  `test_leak_sweep_catches_post_snapshot_launch` (snapshot-complete event).
- `test_deadline_cancel.py` (5): `started` event in each stubborn/cooperative
  task body, awaited before cancel/shutdown.
- `test_auto_resume.py` (2): poll for the resume to land / for the one-shot
  timer to be armed (`_auto_resume_task is not None`).
- `test_cancel_propagation.py` (1): `parent_started` event before cancel.
- `test_shutdown_drain_completion.py` (1): poll `_shutting_down`.

## Remaining work

### 1. `test_cancel_shared_deadline_across_subtree` (confirmed flake, hard)

Confirmed flaky **8/15** isolated. The audit originally missed it because its
`child1_running` / `child2_running` events are **`.set()` by the parent right
after `group.spawn()` returns** — they signal "spawn dispatched", NOT "child is
running with a registered cancellation flag". The `await asyncio.sleep(0.1)`
before `cancel("parent")` then guesses the children registered; under load only
the parent's flag exists, so the post-cancel assertion
`len(deadlines) >= 3` fails with `got 1`.

Why it is *not* a clean fix:

- The assertion inspects **internal executor state**
  (`optio._executor._cancellation_flags`) and asserts the deadlines were stamped
  by **one cancel sweep** (the test's documented intent: "All entries created
  under one cancel sweep share the same monotonic deadline"). So a naive
  poll-after-cancel-until-3 would defeat the very property under test.
- A first attempt — poll until `len(_cancellation_flags) >= 3` *before* cancel —
  only reached 18/20; the 2 fails were the poll itself timing out, i.e. the flag
  count is not a reliable proxy for the precondition. Reverted.

Mechanism (for whoever picks this up): `lifecycle.cancel()` stamps the parent
deadline via `request_cancel_with_deadline`, then awaits
`_cancel_active_children`, which calls `store.list_direct_children(...,
states=ACTIVE_STATES)` — a **DB query** by `parentId`. For all three to receive
the shared deadline, at cancel time each child must be (a) an active row in the
DB with `parentId` set AND (b) present in the executor flag map. The true
precondition is therefore DB-visible active children, not flag-map size.

Candidate approaches (pick in a brainstorming/TDD session):

1. **Poll the DB precondition.** Before cancel, poll `list_direct_children`
   (or `get_process` per child) until both children are in `ACTIVE_STATES` with
   `parentId` set — exactly what `_cancel_active_children` queries. Most honest.
2. **Children set their own running events.** Move the `.set()` into
   `long_child` so the event means "child is actually executing". Needs
   per-child events or a counter, since both children share the `long_child`
   function.
3. **Reconsider the test design.** Question whether asserting on internal
   `_cancellation_flags` is the right altitude at all, vs. asserting the shared
   deadline through an observable surface.

Recommended starting point: approach 1 (matches the real query the code runs;
keeps the one-sweep assertion intact).

### 2. Low-confidence candidates (wide margins, rarely flake)

Same antipattern, but with timing margins wide enough that they seldom flake.
Sweep them with the same "poll the real state" approach; each should become a
bounded poll on the observable, not a fixed sleep:

- `test_auto_resume.py:314` — fixed margin for the fire-and-forget executor to
  advance after a direct `_auto_resume_scheduled_processes()` call; single
  non-retried read.
- `test_no_redis.py:88, 94` — "give the background task a moment to start /
  finish" before a positive state assertion (`launch_fire_and_forget`).
- `test_no_redis.py:107, 110` — assume `running` before cancel, then assume
  `cancelled` before the read (`test_cancel`).
- `test_executor.py:215` — `test_idempotent_launch` assumes the first launch
  reached `running` before the second.

### 3. Systemic guard (optional but recommended)

The audit was conservative and still missed item 1, because the misleading
event *name* (`child_running`) hid that it was set on the wrong side. Consider a
lightweight lint/review checklist or a small grep-based CI check that flags, in
`tests/`, any `asyncio.sleep(<constant>)` that sits between a staging action
(`create_task`, `group.spawn`, `launch`, entering `shutdown`) and an assertion —
so future additions get scrutinized rather than trusted.

## Verifying any fix here

MongoDB on `localhost:27017` (Docker; `excavator-mongodb-1` works). Use a venv
**inside the worktree** (`packages/optio-core/.venv`, editable `.[dev]`). A
single pass proves nothing for a race — hammer the target test ~15–20× isolated,
and ideally once more under concurrent load (run another suite alongside), and
require 0 failures before claiming it fixed.

## Out of scope

- The race-condition tests proper (none identified in optio-core today, but the
  distinction in "Background" governs any future one).
- Test flakiness outside optio-core (e.g. the documented optio-api WS flake
  under `pnpm -r` load — unrelated mechanism).

## Next step

A short TDD session: reproduce item 1 by hammering, apply approach 1, prove
0/20 under load; then sweep item 2 the same way; optionally add item 3.
