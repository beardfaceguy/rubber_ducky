# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja #3 — implement deterministic review lifecycle
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Implement immutable, framework-independent transition functions over the slice-1
message models. The reducer owns round order, status transitions,
conversation-wide B/S/R allocation, open-concern resolution, exact rebuttal and
prior-point coverage, withdrawal, round-3 final position, and escalation-summary
finalization. It performs no I/O and generates no prose.

### Relevant Code / Diff
New file `src/agent_review/lifecycle.py`:

```python
"""Framework-independent state transitions for the review protocol."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agent_review.models import (
    Concern,
    EscalationSummary,
    Rebuttal,
    RebuttalRequest,
    ReviewRequest,
    ReviewResponse,
    Verdict,
)


class ReviewStatus(StrEnum):
    AWAITING_REVIEW_RESPONSE = "awaiting_review_response"
    AWAITING_REBUTTAL = "awaiting_rebuttal"
    AWAITING_FINAL_POSITION = "awaiting_final_position"
    AWAITING_ESCALATION_SUMMARY = "awaiting_escalation_summary"
    APPROVED = "approved"
    WITHDRAWN = "withdrawn"
    ESCALATED = "escalated"


class ReviewState(BaseModel):
    """Immutable aggregate state for one review conversation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ReviewStatus
    request: ReviewRequest
    responses: tuple[ReviewResponse, ...] = Field(default_factory=tuple)
    rebuttals: tuple[Rebuttal, ...] = Field(default_factory=tuple)
    open_blocking_concerns: tuple[Concern, ...] = Field(default_factory=tuple)
    suggestions: tuple[Concern, ...] = Field(default_factory=tuple)
    escalation_summary: EscalationSummary | None = None


class InvalidTransition(ValueError):
    """Raised when a message cannot follow the current review state."""


def _validate_new_concern_ids(
    state: ReviewState,
    response: ReviewResponse,
) -> None:
    for prefix, new_concerns in (
        ("B", response.blocking_concerns),
        ("S", response.suggestions),
    ):
        seen_ids = [
            concern.id
            for prior_response in state.responses
            for concern in (
                prior_response.blocking_concerns
                if prefix == "B"
                else prior_response.suggestions
            )
        ]
        first_expected = len(seen_ids) + 1
        expected_ids = tuple(
            f"{prefix}{number}"
            for number in range(first_expected, first_expected + len(new_concerns))
        )
        actual_ids = tuple(concern.id for concern in new_concerns)
        if actual_ids != expected_ids:
            raise InvalidTransition(
                f"expected new {prefix} IDs {expected_ids}, got {actual_ids}"
            )


def start_review(request: ReviewRequest) -> ReviewState:
    """Start a conversation waiting for its first reviewer response."""

    return ReviewState(
        status=ReviewStatus.AWAITING_REVIEW_RESPONSE,
        request=request,
    )


def apply_review_response(
    state: ReviewState,
    response: ReviewResponse,
) -> ReviewState:
    """Apply a reviewer response to a conversation awaiting one."""

    if state.status is not ReviewStatus.AWAITING_REVIEW_RESPONSE:
        raise InvalidTransition(f"cannot review while status is {state.status.value}")

    expected_round = len(state.responses) + 1
    if response.round != expected_round:
        raise InvalidTransition(
            f"expected review response round {expected_round}, got {response.round}"
        )

    if response.round > 1:
        if len(state.rebuttals) != response.round - 1:
            raise InvalidTransition("review state is missing the prior rebuttal")
        expected_point_ids = tuple(point.id for point in state.rebuttals[-1].new_points)
        addressed_point_ids = tuple(
            prior_response.point_id for prior_response in response.prior_point_responses
        )
        if addressed_point_ids != expected_point_ids:
            raise InvalidTransition(
                "review response must address every point from the prior rebuttal"
            )

    _validate_new_concern_ids(state, response)

    resolved_ids = set(response.resolved_concern_ids)
    open_ids = {concern.id for concern in state.open_blocking_concerns}
    if not resolved_ids <= open_ids:
        unknown_ids = tuple(sorted(resolved_ids - open_ids))
        raise InvalidTransition(
            f"cannot resolve concerns that are not open: {unknown_ids}"
        )
    open_concerns = (
        tuple(
            concern
            for concern in state.open_blocking_concerns
            if concern.id not in resolved_ids
        )
        + response.blocking_concerns
    )

    if response.verdict is Verdict.APPROVE:
        if open_concerns:
            raise InvalidTransition("cannot approve with unresolved blocking concerns")
        next_status = ReviewStatus.APPROVED
    elif response.verdict is Verdict.REVISE:
        if not open_concerns:
            raise InvalidTransition("REVISE requires an unresolved blocking concern")
        next_status = (
            ReviewStatus.AWAITING_FINAL_POSITION
            if response.round == 3
            else ReviewStatus.AWAITING_REBUTTAL
        )
    else:
        if not open_concerns:
            raise InvalidTransition("ESCALATE requires an unresolved blocking concern")
        next_status = ReviewStatus.AWAITING_ESCALATION_SUMMARY

    return state.model_copy(
        update={
            "status": next_status,
            "responses": (*state.responses, response),
            "open_blocking_concerns": open_concerns,
            "suggestions": (*state.suggestions, *response.suggestions),
        }
    )


def apply_rebuttal(state: ReviewState, rebuttal: Rebuttal) -> ReviewState:
    """Apply a worker rebuttal to all currently open blocking concerns."""

    allowed_statuses = {
        ReviewStatus.AWAITING_REBUTTAL,
        ReviewStatus.AWAITING_FINAL_POSITION,
    }
    if state.status not in allowed_statuses:
        raise InvalidTransition(f"cannot rebut while status is {state.status.value}")

    expected_round = len(state.responses)
    if rebuttal.round != expected_round:
        raise InvalidTransition(
            f"expected rebuttal round {expected_round}, got {rebuttal.round}"
        )

    expected_ids = tuple(concern.id for concern in state.open_blocking_concerns)
    response_ids = tuple(
        response.concern_id for response in rebuttal.blocking_responses
    )
    if response_ids != expected_ids:
        raise InvalidTransition("rebuttal must address every open blocking concern")

    seen_point_ids = [
        point.id
        for prior_rebuttal in state.rebuttals
        for point in prior_rebuttal.new_points
    ]
    first_expected = len(seen_point_ids) + 1
    expected_point_ids = tuple(
        f"R{number}"
        for number in range(
            first_expected,
            first_expected + len(rebuttal.new_points),
        )
    )
    actual_point_ids = tuple(point.id for point in rebuttal.new_points)
    if actual_point_ids != expected_point_ids:
        raise InvalidTransition(
            f"expected new R IDs {expected_point_ids}, got {actual_point_ids}"
        )

    if rebuttal.requesting is RebuttalRequest.WITHDRAWN:
        next_status = ReviewStatus.WITHDRAWN
    elif state.status is ReviewStatus.AWAITING_FINAL_POSITION:
        if rebuttal.requesting is not RebuttalRequest.FINAL_POSITION:
            raise InvalidTransition(
                "round three accepts only a final position or withdrawal"
            )
        next_status = ReviewStatus.AWAITING_ESCALATION_SUMMARY
    else:
        if rebuttal.requesting is not RebuttalRequest.RE_REVIEW:
            raise InvalidTransition(
                "rounds one and two must request re-review or withdrawal"
            )
        next_status = ReviewStatus.AWAITING_REVIEW_RESPONSE

    return state.model_copy(
        update={
            "status": next_status,
            "rebuttals": (*state.rebuttals, rebuttal),
        }
    )


def finalize_escalation(
    state: ReviewState,
    summary: EscalationSummary,
) -> ReviewState:
    """Validate and store the worker-owned escalation summary."""

    allowed_statuses = {
        ReviewStatus.AWAITING_FINAL_POSITION,
        ReviewStatus.AWAITING_ESCALATION_SUMMARY,
    }
    if state.status not in allowed_statuses:
        raise InvalidTransition(
            f"cannot finalize escalation while status is {state.status.value}"
        )

    open_ids = tuple(concern.id for concern in state.open_blocking_concerns)
    summary_ids = tuple(
        concern.concern_id for concern in summary.unresolved_blocking_concerns
    )
    if summary_ids != open_ids:
        raise InvalidTransition(
            "escalation summary must include every open blocking concern"
        )

    return state.model_copy(
        update={
            "status": ReviewStatus.ESCALATED,
            "escalation_summary": summary,
        }
    )
```

The complete observable-behavior tests are in `tests/test_lifecycle.py`. They
exercise initialization, approval, round mismatch, malformed prior state,
revision, exact blocker coverage, B/S/R sequence allocation, open-only
resolution, successful resolution and approval, exact prior-point coverage,
withdrawal, round-3 final position, direct reviewer escalation, and exact
summary coverage. The reviewer should inspect that file directly alongside the
implementation.

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ uv run pytest -q
....................................                                     [100%]
36 passed in 0.05s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### Known Concerns
1. `ReviewState` is publicly constructible, so transition functions defensively
   reject malformed sequencing but do not exhaustively validate every possible
   manually fabricated state.
2. Next B/S/R identifiers are derived from immutable history instead of stored
   counters; the protocol limit of three rounds keeps this bounded.
3. Escalation prose is supplied by the worker and validated against exact open
   concern IDs; the reducer does not generate positions or decisions.

### Specific Questions for Reviewer
1. Does every protocol transition have the correct state boundary?
2. Can malformed IDs, concern resolutions, or round ordering enter accepted
   aggregate state through the public transition functions?
3. Is direct escalation finalization from `AWAITING_FINAL_POSITION` consistent
   with the protocol's optional final rebuttal?

## Review Response — Round 1
**Position:** PARTIAL

### Blocking Concerns

B1. The state machine rejects the protocol-legal optional round-3 rebuttal after a round-3 ESCALATE verdict. Protocol overview step 6 says: "If the round-3 response is **not APPROVE**, the worker may append a round-3 rebuttal requesting FINAL-POSITION to record its final position" — a plain reading covers both REVISE and ESCALATE at round 3. In `apply_review_response` (`src/agent_review/lifecycle.py` lines 133–144), only `round == 3` + REVISE yields `AWAITING_FINAL_POSITION`; ESCALATE at any round yields `AWAITING_ESCALATION_SUMMARY`, and `apply_rebuttal` (lines 159–164) rejects that status. So a message sequence the protocol permits — round-3 ESCALATE followed by a FINAL-POSITION rebuttal — raises `InvalidTransition`. This matters because the reducer's entire purpose is deterministic protocol fidelity: rejecting a protocol-legal transition is a correctness defect, not a style choice. I note the counter-reading (the "Escalation report" rule treats "a party issues ESCALATE" as a separate path that goes straight to the summary, and `EscalationSummary.worker_position` records the worker's stance anyway), so a CLARIFY/DISPUTE citing that section would be a reasonable rebuttal — but the ambiguity should be resolved deliberately, not left implicit in the reducer.

### Non-Blocking Suggestions

S1. Two designed behaviors have no test: (a) `finalize_escalation` directly from `AWAITING_FINAL_POSITION` — the exact behavior your Specific Question 3 asks about is exercised nowhere in `tests/test_lifecycle.py` (the escalation tests start from a round-1 ESCALATE, and the round-3 test always passes through the FINAL-POSITION rebuttal first); (b) a WITHDRAWN rebuttal accepted at `AWAITING_FINAL_POSITION` (round-3 withdrawal, which lines 198–199 permit by checking WITHDRAWN before the FINAL-POSITION guard). Add both.

S2. The exact-coverage failures at lines 177 ("rebuttal must address every open blocking concern"), 107–109, and 241–243 raise without saying which IDs were expected versus received, unlike the ID-sequence errors at lines 70–72 and 194–196 which include both tuples. Including expected/actual IDs in all coverage errors would make agent-side debugging of rejected messages materially easier.

### Answers to Specific Questions

1. State boundaries are correct everywhere except the round-3 ESCALATE path (B1). Terminal states reject all messages; `AWAITING_REBUTTAL` is provably reachable only in rounds 1–2, so the round/requesting guards in `apply_rebuttal` and the `Rebuttal` model validators are mutually consistent.
2. No. Through the public transition functions, malformed inputs cannot enter accepted state: B/S/R sequences are validated conversation-wide against immutable history (`_validate_new_concern_ids`, lines 179–196); resolutions must be a subset of currently open concerns (lines 113–119), which also prevents same-response raise-and-resolve; rebuttal and escalation-summary coverage must match open concerns exactly and in order; round numbers must match response/rebuttal counts. The only route in is a hand-built `ReviewState` (your Known Concern 1), which is outside the public transition surface and is defensively checked where cheap (line 100).
3. Yes — `finalize_escalation` accepting `AWAITING_FINAL_POSITION` correctly models the protocol's "may append" (the rebuttal is optional, and `worker_position` in the summary still records the final stance). But see B1 for the mirror-image case, and S1(a) for the missing test.

**Verdict:** REVISE

## Rebuttal — Round 1
**Position:** AGREE

### Responses to Blocking Concerns
Re B1: ACCEPT — overview step 6 applies to every non-APPROVE round-3
response. Round-3 ESCALATE now enters `AWAITING_FINAL_POSITION`, allowing
either a FINAL-POSITION rebuttal or direct escalation finalization.

### Revised Code / Diff
```diff
--- a/src/agent_review/lifecycle.py
+++ b/src/agent_review/lifecycle.py
@@
         if addressed_point_ids != expected_point_ids:
             raise InvalidTransition(
-                "review response must address every point from the prior rebuttal"
+                "review response must address every point from the prior rebuttal: "
+                f"expected {expected_point_ids}, got {addressed_point_ids}"
             )
@@
     else:
         if not open_concerns:
             raise InvalidTransition("ESCALATE requires an unresolved blocking concern")
-        next_status = ReviewStatus.AWAITING_ESCALATION_SUMMARY
+        next_status = (
+            ReviewStatus.AWAITING_FINAL_POSITION
+            if response.round == 3
+            else ReviewStatus.AWAITING_ESCALATION_SUMMARY
+        )
@@
     if response_ids != expected_ids:
-        raise InvalidTransition("rebuttal must address every open blocking concern")
+        raise InvalidTransition(
+            "rebuttal must address every open blocking concern: "
+            f"expected {expected_ids}, got {response_ids}"
+        )
@@
     if summary_ids != open_ids:
         raise InvalidTransition(
-            "escalation summary must include every open blocking concern"
+            "escalation summary must include every open blocking concern: "
+            f"expected {open_ids}, got {summary_ids}"
         )

--- a/tests/test_lifecycle.py
+++ b/tests/test_lifecycle.py
@@
+def test_round_three_escalate_allows_optional_final_position() -> None:
+    state = await_second_response()
+    state = apply_review_response(
+        state,
+        ReviewResponse(
+            round=2,
+            position=Position.DISAGREE,
+            verdict=Verdict.REVISE,
+        ),
+    )
+    state = apply_rebuttal(
+        state,
+        Rebuttal(
+            round=2,
+            position=Position.DISAGREE,
+            blocking_responses=(
+                BlockingConcernResponse(
+                    concern_id="B1",
+                    disposition=Disposition.DISPUTE,
+                    reason="The blocker remains disputed.",
+                ),
+            ),
+            revised_diff="Unchanged — see Review Request.",
+            requesting=RebuttalRequest.RE_REVIEW,
+        ),
+    )
+
+    state = apply_review_response(
+        state,
+        ReviewResponse(
+            round=3,
+            position=Position.DISAGREE,
+            verdict=Verdict.ESCALATE,
+        ),
+    )
+    assert state.status is ReviewStatus.AWAITING_FINAL_POSITION
+
+    withdrawn = apply_rebuttal(
+        state,
+        Rebuttal(
+            round=3,
+            position=Position.DISAGREE,
+            blocking_responses=(
+                BlockingConcernResponse(
+                    concern_id="B1",
+                    disposition=Disposition.DISPUTE,
+                    reason="The worker withdraws instead of escalating.",
+                ),
+            ),
+            revised_diff="Unchanged — see Review Request.",
+            requesting=RebuttalRequest.WITHDRAWN,
+        ),
+    )
+    assert withdrawn.status is ReviewStatus.WITHDRAWN
@@
     assert state.status is ReviewStatus.AWAITING_FINAL_POSITION
+
+    direct_summary = EscalationSummary(
+        unresolved_blocking_concerns=(
+            EscalationConcern(concern_id="B1", status="Still disputed."),
+        ),
+        worker_position="Final worker position.",
+        reviewer_position="The blocker remains.",
+        decision_needed="Should implementation continue?",
+    )
+    directly_escalated = finalize_escalation(state, direct_summary)
+    assert directly_escalated.status is ReviewStatus.ESCALATED
```

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ uv run pytest -q
.....................................                                    [100%]
37 passed in 0.07s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### New Points
R1. Adopted S1 by testing direct escalation finalization from
`AWAITING_FINAL_POSITION` and round-3 withdrawal.

R2. Adopted S2 by including expected and received IDs in all exact-coverage
transition errors.

**Requesting:** RE-REVIEW

## Review Response — Round 2
**Position:** AGREE

### Blocking Concerns
None.

### Non-Blocking Suggestions
None. (S1 and S2 from Round 1 are adopted; see below.)

### Responses to Prior Points

Re R1: Confirmed against the actual test file. `tests/test_lifecycle.py` now exercises both behaviors S1 requested: (a) direct `finalize_escalation` from `AWAITING_FINAL_POSITION` (lines 427–436 inside `test_round_three_revise_accepts_final_position_then_requires_summary`, asserting `ESCALATED`), and (b) a WITHDRAWN rebuttal accepted at `AWAITING_FINAL_POSITION` (lines 496–512 in `test_round_three_escalate_allows_optional_final_position`, asserting `WITHDRAWN`). The immutability of `ReviewState` makes the branch-and-reuse of `state` in the first test sound. S1 is addressed.

Re R2: Confirmed against `src/agent_review/lifecycle.py`. All three exact-coverage errors now include both tuples: prior-point coverage (lines 106–110), rebuttal blocker coverage (lines 181–185), and escalation-summary coverage (lines 248–252). Together with the pre-existing ID-sequence errors, every `InvalidTransition` that compares ID tuples now reports expected versus received. S2 is addressed.

B1: resolved. In `apply_review_response`, the ESCALATE branch (lines 142–149) now routes `round == 3` to `AWAITING_FINAL_POSITION`, mirroring the REVISE branch, so the protocol-legal sequence from overview step 6 — round-3 non-APPROVE followed by an optional FINAL-POSITION rebuttal — is accepted for both REVISE and ESCALATE. I verified the full downstream lattice from that status: `apply_rebuttal` accepts FINAL-POSITION (→ `AWAITING_ESCALATION_SUMMARY`) or WITHDRAWN (→ `WITHDRAWN`), and `finalize_escalation` accepts the status directly for workers who skip the optional rebuttal (lines 235–238) — "may append" is correctly modeled as optional in both directions now. Rounds 1–2 ESCALATE still go straight to `AWAITING_ESCALATION_SUMMARY`, which matches the protocol's separate "a party issues ESCALATE" path outside the round-3 special case. The new test asserts round-3 ESCALATE lands in `AWAITING_FINAL_POSITION`; it exits via WITHDRAWN rather than FINAL-POSITION, but since `apply_rebuttal` discriminates only on status and the FINAL-POSITION exit from that same status is covered by the round-3 REVISE test, the coverage is adequate — no residual concern.

Verification: I inspected the actual revised `lifecycle.py`, `models.py`, and `test_lifecycle.py` line by line; the code matches the rebuttal's diff exactly, and the worker's reported verification (ruff clean, 37 tests passing, build succeeding) is consistent with what the files contain. No new defects were introduced by the revision: the round guard in `apply_rebuttal` (`expected_round = len(state.responses)`) still yields 3 on the new path, and terminal states remain unreachable by further messages.

**Verdict:** APPROVE
