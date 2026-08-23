# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja #11 — add validated review-state replay
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Treat the initial request and ordered protocol messages as the persistence source
of truth. `replay_review` starts from a clean aggregate and dispatches every
validated event through the existing transition functions. Invalid history is
rejected with its one-based event index and type. Derived `ReviewState` is never
accepted as replay input.

### Relevant Code / Diff
```diff
--- a/src/agent_review/lifecycle.py
+++ b/src/agent_review/lifecycle.py
@@
+from collections.abc import Iterable
 from enum import StrEnum
@@
 class InvalidTransition(ValueError):
     """Raised when a message cannot follow the current review state."""


+ReviewEvent = ReviewResponse | Rebuttal | EscalationSummary
+
+
@@
+def replay_review(
+    request: ReviewRequest,
+    events: Iterable[ReviewEvent],
+) -> ReviewState:
+    """Rebuild canonical state by replaying validated protocol messages."""
+
+    state = start_review(request)
+    for index, event in enumerate(events, start=1):
+        try:
+            if isinstance(event, ReviewResponse):
+                state = apply_review_response(state, event)
+            elif isinstance(event, Rebuttal):
+                state = apply_rebuttal(state, event)
+            elif isinstance(event, EscalationSummary):
+                state = finalize_escalation(state, event)
+            else:
+                raise InvalidTransition(
+                    f"unsupported review event type: {type(event).__name__}"
+                )
+        except InvalidTransition as error:
+            raise InvalidTransition(
+                f"invalid event {index} ({type(event).__name__}): {error}"
+            ) from error
+    return state

--- a/tests/test_lifecycle.py
+++ b/tests/test_lifecycle.py
@@
+def test_replay_reconstructs_canonical_state() -> None:
+    request = make_request()
+    response = ReviewResponse(
+        round=1,
+        position=Position.AGREE,
+        verdict=Verdict.APPROVE,
+    )
+    expected = apply_review_response(start_review(request), response)
+
+    replayed = replay_review(request, (response,))
+
+    assert replayed == expected
+
+
+def test_replay_rejects_invalid_order_with_event_context() -> None:
+    rebuttal = Rebuttal(
+        round=1,
+        position=Position.DISAGREE,
+        blocking_responses=(
+            BlockingConcernResponse(
+                concern_id="B1",
+                disposition=Disposition.DISPUTE,
+                reason="Out-of-order rebuttal.",
+            ),
+        ),
+        revised_diff="Unchanged — see Review Request.",
+        requesting=RebuttalRequest.RE_REVIEW,
+    )
+
+    with pytest.raises(
+        InvalidTransition,
+        match=r"invalid event 1 \(Rebuttal\)",
+    ):
+        replay_review(make_request(), (rebuttal,))
+
+
+def test_replay_dispatches_escalation_summary() -> None:
+    response = ReviewResponse(
+        round=1,
+        position=Position.DISAGREE,
+        blocking_concerns=(
+            Concern(id="B1", kind=ConcernKind.BLOCKING, text="Deadlocked."),
+        ),
+        verdict=Verdict.ESCALATE,
+    )
+    summary = EscalationSummary(
+        unresolved_blocking_concerns=(
+            EscalationConcern(concern_id="B1", status="Still disputed."),
+        ),
+        worker_position="The implementation is safe.",
+        reviewer_position="The blocker remains.",
+        decision_needed="Should implementation continue?",
+    )
+
+    replayed = replay_review(make_request(), (response, summary))
+
+    assert replayed.status is ReviewStatus.ESCALATED
+    assert replayed.escalation_summary == summary
```

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ uv run pytest -q
........................................                                 [100%]
40 passed in 0.05s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### Known Concerns
1. Replay accepts validated Pydantic objects; parsing and discriminating raw
   persisted JSON remains a persistence-layer responsibility.
2. The unsupported-event branch is a runtime defense for callers that bypass
   static typing.
3. Replay consumes iterables once and stops at the first invalid event.

### Specific Questions for Reviewer
1. Does replay make derived state safely reconstructible without duplicating
   lifecycle rules?
2. Are all event types dispatched through the correct transition function?
3. Does error wrapping preserve enough cause and position information for
   persistence diagnostics?

## Review Response — Round 1
**Position:** AGREE

Verification note: I inspected the actual source rather than the diff alone. `replay_review` matches the submitted diff exactly, dispatches only through the existing transition functions (`start_review`, `apply_review_response`, `apply_rebuttal`, `finalize_escalation`), and duplicates no lifecycle rules. Terminal states (APPROVED, WITHDRAWN, ESCALATED) correctly reject further events because the underlying transitions already guard on `state.status`. The three event classes are disjoint (no inheritance among them), so `isinstance` dispatch order cannot misroute an event. Error wrapping preserves the one-based index, the concrete event type name, the original message, and the full cause chain via `raise ... from error`.

Answers to the specific questions:
1. Yes. Replay's only logic is dispatch and error contextualization; every validity rule lives in the existing transitions, and the signature (`ReviewRequest` + events) makes it impossible to seed replay from a derived `ReviewState`.
2. Yes. The event union has exactly three members and each is routed to its correct transition; `ReviewRequest` is correctly excluded from the event stream and consumed by `start_review`.
3. Yes for diagnostics: index, event type, original message, and `__cause__` are all preserved. See S3 for one cosmetic redundancy.

### Blocking Concerns
None.

### Non-Blocking Suggestions
1. S1 — The successful `apply_rebuttal` dispatch path is never exercised by a replay test: `test_replay_rejects_invalid_order_with_event_context` hits the rebuttal branch only on its error path, and no test replays a full multi-round sequence (response → rebuttal → response). A single end-to-end replay test comparing against sequentially applied transitions would guard the dispatch table against regressions.
2. S2 — The unsupported-event branch (lines 281–284) is untested. Since the request explicitly frames it as a runtime defense for callers bypassing static typing, a one-line test replaying e.g. a plain string would pin its behavior.
3. S3 — Because the unsupported-type `InvalidTransition` is raised inside the `try`, it gets re-wrapped by the same `except`, yielding messages like `invalid event 2 (str): unsupported review event type: str` — the type name appears twice. Harmless; raising it outside the wrapped region (or accepting the redundancy) is purely cosmetic.

**Verdict:** APPROVE

## Post-Approval Follow-Up
S1 and S2 were adopted at the operator's request to harden replay coverage.
`test_replay_dispatches_complete_multi_round_history` now exercises successful
response → rebuttal → response dispatch and compares replay against sequential
reduction. `test_replay_rejects_unsupported_runtime_event` pins the defensive
runtime error. S3 was left unchanged because the duplicated type name is useful
context at both the event and cause levels. Ruff passes, 42 tests pass, and the
package builds successfully.
