# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja #2 — define typed review protocol models
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Add immutable Pydantic models for protocol requests, responses, rebuttals,
concerns, IDs, dispositions, positions, and verdicts. Validation is deliberately
limited to facts available within one message; conversation-wide state and
transitions remain slice 2. Incorporate the prior review's three wording
clarifications because they define valid message values.

### Relevant Code / Diff
`src/agent_review/models.py`:

```python
"""Validated value objects for review-protocol.md v1.3."""

from enum import StrEnum
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RoundNumber = Annotated[int, Field(ge=1, le=3)]


def _numbers_are_increasing(ids: list[str]) -> bool:
    numbers = [int(identifier[1:]) for identifier in ids]
    return all(left < right for left, right in pairwise(numbers))


class ProtocolModel(BaseModel):
    """Base configuration shared by protocol values."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ConcernKind(StrEnum):
    BLOCKING = "blocking"
    SUGGESTION = "suggestion"


class Position(StrEnum):
    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    PARTIAL = "PARTIAL"


class Verdict(StrEnum):
    APPROVE = "APPROVE"
    REVISE = "REVISE"
    ESCALATE = "ESCALATE"


class Disposition(StrEnum):
    ACCEPT = "ACCEPT"
    DISPUTE = "DISPUTE"
    CLARIFY = "CLARIFY"


class RebuttalRequest(StrEnum):
    RE_REVIEW = "RE-REVIEW"
    WITHDRAWN = "WITHDRAWN"
    FINAL_POSITION = "FINAL-POSITION"


class Concern(ProtocolModel):
    """A reviewer-owned blocking concern or non-blocking suggestion."""

    id: Annotated[str, Field(pattern=r"^[BS][1-9][0-9]*$")]
    kind: ConcernKind
    text: NonEmptyText

    @model_validator(mode="after")
    def id_prefix_matches_kind(self) -> "Concern":
        expected_prefix = "B" if self.kind is ConcernKind.BLOCKING else "S"
        if not self.id.startswith(expected_prefix):
            raise ValueError(
                f"{self.kind.value} concern IDs must start with {expected_prefix}"
            )
        return self


class PriorPointResponse(ProtocolModel):
    """A reviewer response to a worker-owned rebuttal point."""

    point_id: Annotated[str, Field(pattern=r"^R[1-9][0-9]*$")]
    response: NonEmptyText


class BlockingConcernResponse(ProtocolModel):
    """A worker's disposition and reasoning for one blocking concern."""

    concern_id: Annotated[str, Field(pattern=r"^B[1-9][0-9]*$")]
    disposition: Disposition
    reason: NonEmptyText


class RebuttalPoint(ProtocolModel):
    """A worker-owned point introduced in a rebuttal."""

    id: Annotated[str, Field(pattern=r"^R[1-9][0-9]*$")]
    text: NonEmptyText


class Rebuttal(ProtocolModel):
    """A worker response to a REVISE verdict."""

    round: RoundNumber
    position: Position
    blocking_responses: tuple[BlockingConcernResponse, ...] = Field(min_length=1)
    revised_diff: NonEmptyText
    new_points: tuple[RebuttalPoint, ...] = Field(default_factory=tuple)
    requesting: RebuttalRequest

    @model_validator(mode="after")
    def accepted_concerns_include_revised_diff(self) -> "Rebuttal":
        blocking_ids = [response.concern_id for response in self.blocking_responses]
        point_ids = [point.id for point in self.new_points]
        if len(blocking_ids) != len(set(blocking_ids)):
            raise ValueError("each blocking concern may be answered only once")
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("rebuttal point IDs must be unique")
        if not _numbers_are_increasing(blocking_ids) or not _numbers_are_increasing(
            point_ids
        ):
            raise ValueError("rebuttal IDs must be in monotonically increasing order")
        accepted = any(
            response.disposition is Disposition.ACCEPT
            for response in self.blocking_responses
        )
        if accepted and self.revised_diff == "Unchanged — see Review Request.":
            raise ValueError("accepted concerns require an actual revised diff")
        if self.round == 3 and self.requesting is RebuttalRequest.RE_REVIEW:
            raise ValueError("round-three rebuttals cannot request another review")
        if self.round < 3 and self.requesting is RebuttalRequest.FINAL_POSITION:
            raise ValueError("FINAL-POSITION is valid only in round three")
        return self


class ReviewResponse(ProtocolModel):
    """A reviewer's response for one of the three allowed rounds."""

    round: RoundNumber
    position: Position
    blocking_concerns: tuple[Concern, ...] = Field(default_factory=tuple)
    suggestions: tuple[Concern, ...] = Field(default_factory=tuple)
    resolved_concern_ids: tuple[
        Annotated[str, Field(pattern=r"^B[1-9][0-9]*$")], ...
    ] = Field(default_factory=tuple)
    prior_point_responses: tuple[PriorPointResponse, ...] = Field(default_factory=tuple)
    verdict: Verdict

    @model_validator(mode="after")
    def verdict_matches_concerns(self) -> "ReviewResponse":
        if any(
            concern.kind is not ConcernKind.BLOCKING
            for concern in self.blocking_concerns
        ):
            raise ValueError("blocking_concerns may contain only blocking concerns")
        if any(
            concern.kind is not ConcernKind.SUGGESTION for concern in self.suggestions
        ):
            raise ValueError("suggestions may contain only suggestions")
        if self.round == 1 and self.prior_point_responses:
            raise ValueError("round-one responses cannot address prior rebuttal points")
        prior_point_ids = [response.point_id for response in self.prior_point_responses]
        if len(prior_point_ids) != len(set(prior_point_ids)):
            raise ValueError("each rebuttal point may be answered only once")
        if not _numbers_are_increasing(prior_point_ids):
            raise ValueError(
                "rebuttal point IDs must be in monotonically increasing order"
            )
        ids = [
            *(concern.id for concern in self.blocking_concerns),
            *(concern.id for concern in self.suggestions),
            *self.resolved_concern_ids,
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("concern IDs must be unique within a response")
        for concern_ids in (
            [concern.id for concern in self.blocking_concerns],
            [concern.id for concern in self.suggestions],
            list(self.resolved_concern_ids),
        ):
            if not _numbers_are_increasing(concern_ids):
                raise ValueError(
                    "concern IDs must be in monotonically increasing order"
                )
        if self.verdict is Verdict.APPROVE and self.blocking_concerns:
            raise ValueError("APPROVE responses cannot raise blocking concerns")
        return self


class ReviewRequest(ProtocolModel):
    """The initial request that starts review round one."""

    protocol_version: Literal["1.3"] = "1.3"
    round: Literal[1] = 1
    task_id: NonEmptyText
    title: NonEmptyText
    proposed_solution: NonEmptyText
    relevant_diff: NonEmptyText
    known_concerns: tuple[NonEmptyText, ...] = Field(default_factory=tuple)
    questions: tuple[NonEmptyText, ...] = Field(default_factory=tuple)
```

`tests/test_models.py`:

```python
import pytest
from pydantic import ValidationError

from agent_review.models import (
    BlockingConcernResponse,
    Concern,
    ConcernKind,
    Disposition,
    Position,
    PriorPointResponse,
    Rebuttal,
    RebuttalPoint,
    RebuttalRequest,
    ReviewRequest,
    ReviewResponse,
    Verdict,
)


def test_review_request_only_allows_round_one() -> None:
    with pytest.raises(ValidationError):
        ReviewRequest(
            round=2,
            task_id="AR-1",
            title="Add typed protocol models",
            proposed_solution="Represent protocol messages with validated models.",
            relevant_diff="+class ReviewRequest: ...",
        )


def test_concern_id_prefix_must_match_kind() -> None:
    with pytest.raises(ValidationError):
        Concern(id="S1", kind=ConcernKind.BLOCKING, text="State can skip review.")


def test_approve_response_cannot_raise_blocking_concerns() -> None:
    with pytest.raises(ValidationError):
        ReviewResponse(
            round=1,
            position=Position.AGREE,
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="A blocker remains."),
            ),
            verdict=Verdict.APPROVE,
        )


def test_response_rejects_concern_in_wrong_collection() -> None:
    with pytest.raises(ValidationError):
        ReviewResponse(
            round=1,
            position=Position.PARTIAL,
            suggestions=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="A blocker."),
            ),
            verdict=Verdict.REVISE,
        )


def test_response_rejects_duplicate_concern_ids() -> None:
    with pytest.raises(ValidationError):
        ReviewResponse(
            round=1,
            position=Position.PARTIAL,
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="First blocker."),
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="Duplicate ID."),
            ),
            verdict=Verdict.REVISE,
        )


def test_response_requires_monotonically_ordered_concern_ids() -> None:
    with pytest.raises(ValidationError):
        ReviewResponse(
            round=1,
            position=Position.PARTIAL,
            blocking_concerns=(
                Concern(id="B2", kind=ConcernKind.BLOCKING, text="Second blocker."),
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="First blocker."),
            ),
            verdict=Verdict.REVISE,
        )


def test_round_one_response_rejects_prior_point_responses() -> None:
    with pytest.raises(ValidationError):
        ReviewResponse(
            round=1,
            position=Position.AGREE,
            prior_point_responses=(
                PriorPointResponse(point_id="R1", response="Addressed."),
            ),
            verdict=Verdict.APPROVE,
        )


def test_accepting_concern_requires_revised_diff() -> None:
    with pytest.raises(ValidationError):
        Rebuttal(
            round=1,
            position=Position.AGREE,
            blocking_responses=(
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.ACCEPT,
                    reason="The missing validation is real.",
                ),
            ),
            revised_diff="Unchanged — see Review Request.",
            requesting=RebuttalRequest.RE_REVIEW,
        )


def test_round_three_rebuttal_cannot_request_re_review() -> None:
    with pytest.raises(ValidationError):
        Rebuttal(
            round=3,
            position=Position.DISAGREE,
            blocking_responses=(
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.DISPUTE,
                    reason="The cited path is unreachable.",
                ),
            ),
            revised_diff="Unchanged — see Review Request.",
            requesting=RebuttalRequest.RE_REVIEW,
        )


def test_round_three_allows_final_position() -> None:
    rebuttal = Rebuttal(
        round=3,
        position=Position.DISAGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.DISPUTE,
                reason="The cited path is unreachable.",
            ),
        ),
        revised_diff="Unchanged — see Review Request.",
        requesting=RebuttalRequest.FINAL_POSITION,
    )

    assert rebuttal.requesting is RebuttalRequest.FINAL_POSITION


def test_rebuttal_rejects_duplicate_blocking_response_ids() -> None:
    with pytest.raises(ValidationError):
        Rebuttal(
            round=1,
            position=Position.PARTIAL,
            blocking_responses=(
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.ACCEPT,
                    reason="The first concern is valid.",
                ),
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.DISPUTE,
                    reason="Duplicate response.",
                ),
            ),
            revised_diff="+validation = True",
            requesting=RebuttalRequest.RE_REVIEW,
        )


def test_response_requires_monotonically_ordered_prior_point_ids() -> None:
    with pytest.raises(ValidationError):
        ReviewResponse(
            round=2,
            position=Position.PARTIAL,
            prior_point_responses=(
                PriorPointResponse(point_id="R2", response="Second point."),
                PriorPointResponse(point_id="R1", response="First point."),
            ),
            verdict=Verdict.REVISE,
        )


def test_rebuttal_requires_monotonically_ordered_new_point_ids() -> None:
    with pytest.raises(ValidationError):
        Rebuttal(
            round=1,
            position=Position.DISAGREE,
            blocking_responses=(
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.DISPUTE,
                    reason="The concern does not apply.",
                ),
            ),
            revised_diff="Unchanged — see Review Request.",
            new_points=(
                RebuttalPoint(id="R2", text="Second point."),
                RebuttalPoint(id="R1", text="First point."),
            ),
            requesting=RebuttalRequest.RE_REVIEW,
        )


def test_protocol_models_are_immutable() -> None:
    request = ReviewRequest(
        task_id="AR-1",
        title="Add typed protocol models",
        proposed_solution="Represent protocol messages with validated models.",
        relevant_diff="+class ReviewRequest: ...",
    )

    with pytest.raises(ValidationError):
        request.title = "Changed"  # type: ignore[misc]
```

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "agent-review"
version = "0.1.0"
description = "Deterministic protocol models for agent-to-agent code review"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.13.4",
]

[tool.pytest.ini_options]
testpaths = ["tests"]

[dependency-groups]
dev = [
    "pytest>=9.1.1",
]
```

Protocol clarification diff:

```diff
@@ Changelog
-- v1.3 — Defined exact round counting and ID allocation, and required reviewer
-  verification of accepted revisions before consensus.
+- v1.3 — Defined exact round counting, terminal message values, and ID
+  allocation, and required reviewer verification before consensus.

@@ Overview
-6. If the round-3 response is not APPROVE, the worker may append a round-3
-   rebuttal to record its final position, then records the deadlock and
-   **escalates to the human operator**. The reviewer must not send a round-4
-   response.
+6. If the round-3 response is not APPROVE, the worker may append a round-3
+   rebuttal requesting FINAL-POSITION to record its final position, then
+   records the deadlock and **escalates to the human operator**. The reviewer
+   must not send a round-4 response.

@@ Review Request template
-## Review Request — Round N
+## Review Request — Round 1

@@ Rebuttal template
-**Requesting:** RE-REVIEW | WITHDRAWN
+**Requesting:** RE-REVIEW | WITHDRAWN | FINAL-POSITION
+<RE-REVIEW is valid only in rounds 1-2. FINAL-POSITION is valid only in round
+3. WITHDRAWN is valid in any round.>

@@ Concern resolution
-  "B<n>: resolved" after inspecting the revised code or diff.
+  "B<n>: resolved" after inspecting the rebuttal and any revised code or diff.
```

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ uv run pytest -q
..............                                                           [100%]
14 passed in 0.04s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### Known Concerns
1. Conversation-wide ID allocation and unresolved-concern state are intentionally
   deferred to the lifecycle reducer in slice 2.
2. Revised-diff detection rejects the protocol's exact unchanged marker; it
   does not attempt to parse or semantically validate a diff.
3. Generated `uv.lock` is not reproduced above; the reviewer can inspect the
   complete lockfile in the workspace.

### Specific Questions for Reviewer
1. Do the models faithfully represent protocol v1.3 without absorbing lifecycle
   responsibilities?
2. Are any per-message invariants missing or incorrectly enforced?
3. Are the tests sufficient for the validation behavior introduced by this slice?

## Review Response — Round 1
**Position:** PARTIAL

### Blocking Concerns

B1. `ReviewResponse` accepts `resolved_concern_ids` in round 1. Verified against the actual code at `src/agent_review/models.py` (the `verdict_matches_concerns` validator, lines 140–178): `ReviewResponse(round=1, position=AGREE, resolved_concern_ids=("B1",), verdict=APPROVE)` constructs without error. This is invalid per protocol — resolution ("B<n>: resolved") happens only "after inspecting the rebuttal and any revised code or diff" (Rules → Concern tracking), and no rebuttal or concern can precede round 1. This is a per-message invariant, not slice-2 lifecycle state: it depends only on `round` and a field of the same message, exactly like the round-1 `prior_point_responses` rejection the slice already enforces at line 151–152. Enforcing one but not the other means the model type still admits invalid round-1 messages, which defeats the slice's purpose.

B2. `ReviewResponse` accepts a round-1 REVISE with zero blocking concerns. Verified: `ReviewResponse(round=1, position=DISAGREE, verdict=REVISE)` constructs with `blocking_concerns == ()`. The protocol defines REVISE as "At least one unresolved blocking concern" (Rules → Verdicts). In rounds 2–3 unresolved concerns can carry over from prior rounds, so emptiness there is legitimately undecidable per-message — but in round 1 the only concerns in existence are the ones in this message, so `round == 1 and verdict is REVISE` requires `blocking_concerns` to be non-empty. Same scoping argument as B1: decidable from one message, so it belongs in this slice.

### Non-Blocking Suggestions

S1. The test suite is almost entirely negative-path: of 14 tests, only `test_round_three_allows_final_position` and `test_protocol_models_are_immutable` construct a valid message, and no test constructs a valid `ReviewResponse` at all. One happy-path round-2 response exercising the most complex validator (blocking concerns + suggestions + resolved IDs + prior-point responses together) would guard against the validator over-rejecting valid messages — a failure mode the current suite cannot detect. This also answers your Question 3: sufficient for the rejections you wrote, but not for acceptance behavior.

S2. The Escalation Summary (protocol section "Escalation report") is the one protocol message with no model. If it is intentionally deferred with the lifecycle reducer, record that in the slice-2 scope so it isn't dropped; it is a message format, not conversation state, so its absence here is a scope call worth making explicit rather than an omission.

Answering your remaining questions: (Q1) yes — the models otherwise stay cleanly on the per-message side of the line; enum values, ID patterns, the round-3 `RE-REVIEW`/`FINAL-POSITION` gating, and the accepted-diff requirement all match protocol v1.3 as amended by your clarification diff, which I confirmed is applied verbatim to `references/review-protocol.md`. Your Known Concern 2 (exact-match sentinel for the unchanged marker) is acceptable for this slice given `NonEmptyText` strips surrounding whitespace; I am not raising it. `pyproject.toml` and `uv.lock` are consistent (pydantic 2.13.4, pytest 9.1.1, editable root package), and the suite passes as claimed: 14 passed in 0.04s.

**Verdict:** REVISE

## Rebuttal — Round 1
**Position:** AGREE

### Responses to Blocking Concerns
Re B1: ACCEPT — resolution requires a prior rebuttal, so a round-1 response
cannot validly contain resolved concern IDs. The model now rejects that case.

Re B2: ACCEPT — round 1 has no carried state, so REVISE must introduce at
least one blocking concern in the same response. The model now enforces that
round-specific invariant.

### Revised Code / Diff
```diff
--- a/src/agent_review/models.py
+++ b/src/agent_review/models.py
@@
         if self.round == 1 and self.prior_point_responses:
             raise ValueError("round-one responses cannot address prior rebuttal points")
+        if self.round == 1 and self.resolved_concern_ids:
+            raise ValueError("round-one responses cannot resolve prior concerns")
@@
         if self.verdict is Verdict.APPROVE and self.blocking_concerns:
             raise ValueError("APPROVE responses cannot raise blocking concerns")
+        if (
+            self.round == 1
+            and self.verdict is Verdict.REVISE
+            and not self.blocking_concerns
+        ):
+            raise ValueError("round-one REVISE responses require a blocking concern")
         return self
+
+
+class EscalationConcern(ProtocolModel):
+    """An unresolved blocking concern included in an escalation."""
+
+    concern_id: Annotated[str, Field(pattern=r"^B[1-9][0-9]*$")]
+    status: NonEmptyText
+
+
+class EscalationSummary(ProtocolModel):
+    """The final positions and decision requested from the operator."""
+
+    unresolved_blocking_concerns: tuple[EscalationConcern, ...] = Field(min_length=1)
+    worker_position: NonEmptyText
+    reviewer_position: NonEmptyText
+    decision_needed: NonEmptyText
+
+    @model_validator(mode="after")
+    def concern_ids_are_unique_and_ordered(self) -> "EscalationSummary":
+        concern_ids = [
+            concern.concern_id for concern in self.unresolved_blocking_concerns
+        ]
+        if len(concern_ids) != len(set(concern_ids)):
+            raise ValueError("escalation concern IDs must be unique")
+        if not _numbers_are_increasing(concern_ids):
+            raise ValueError(
+                "escalation concern IDs must be in monotonically increasing order"
+            )
+        return self

--- a/tests/test_models.py
+++ b/tests/test_models.py
@@
+def test_round_one_response_cannot_resolve_concerns() -> None:
+    with pytest.raises(ValidationError):
+        ReviewResponse(
+            round=1,
+            position=Position.AGREE,
+            resolved_concern_ids=("B1",),
+            verdict=Verdict.APPROVE,
+        )
+
+
+def test_round_one_revise_requires_blocking_concern() -> None:
+    with pytest.raises(ValidationError):
+        ReviewResponse(
+            round=1,
+            position=Position.DISAGREE,
+            verdict=Verdict.REVISE,
+        )
+
+
+def test_round_two_accepts_complete_valid_response() -> None:
+    response = ReviewResponse(
+        round=2,
+        position=Position.PARTIAL,
+        blocking_concerns=(
+            Concern(id="B2", kind=ConcernKind.BLOCKING, text="A new blocker."),
+        ),
+        suggestions=(
+            Concern(id="S1", kind=ConcernKind.SUGGESTION, text="Optional cleanup."),
+        ),
+        resolved_concern_ids=("B1",),
+        prior_point_responses=(
+            PriorPointResponse(point_id="R1", response="The evidence resolves B1."),
+        ),
+        verdict=Verdict.REVISE,
+    )
+
+    assert response.resolved_concern_ids == ("B1",)
+
+
+def test_escalation_summary_requires_blocking_concern_ids() -> None:
+    with pytest.raises(ValidationError):
+        EscalationSummary(
+            unresolved_blocking_concerns=(
+                EscalationConcern(concern_id="R1", status="Still disputed."),
+            ),
+            worker_position="The implementation is safe.",
+            reviewer_position="The race remains.",
+            decision_needed="Should the implementation proceed?",
+        )
```

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ uv run pytest -q
..................                                                       [100%]
18 passed in 0.05s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### New Points
R1. Adopted S1 by adding a valid round-2 response covering new blockers,
suggestions, resolved concerns, and responses to prior points.

R2. Adopted S2 by adding typed `EscalationConcern` and `EscalationSummary`
models; escalation triggering and population remain slice-2 responsibilities.

**Requesting:** RE-REVIEW

## Review Response — Round 2
**Position:** AGREE

### Blocking Concerns
None.

### Non-Blocking Suggestions
S3. `EscalationSummary` has only a negative-path test (`test_escalation_summary_requires_blocking_concern_ids`, which exercises the `EscalationConcern.concern_id` pattern rather than the summary's own validator). A single valid construction — two concerns, ordered IDs — would cover the acceptance side of `concern_ids_are_unique_and_ordered`, the same gap S1 identified for `ReviewResponse`. Optional; do not hold the slice for it.

### Responses to Prior Points
Re R1: Confirmed. `test_round_two_accepts_complete_valid_response` (`tests/test_models.py` lines 119–136) constructs a round-2 response exercising all four collections simultaneously — a new blocker (B2), a suggestion (S1), a resolved ID (B1), and a prior-point response (R1) — and it passes against the real validator. This closes the acceptance-behavior gap S1 described; S1 is adopted.

Re R2: Confirmed. `EscalationConcern` and `EscalationSummary` (`src/agent_review/models.py` lines 189–215) match the protocol's Escalation Summary template: at-least-one unresolved blocking concern with per-concern status, worker position, reviewer position, and decision needed, with uniqueness and monotonic ordering enforced on concern IDs. Leaving escalation triggering to the slice-2 reducer is the correct boundary — whether escalation is warranted is conversation state; the message shape is not. S2 is adopted.

B1: resolved. Verified against the actual code at `src/agent_review/models.py` lines 153–154: `ReviewResponse(round=1, ..., resolved_concern_ids=("B1",), verdict=APPROVE)` now raises "round-one responses cannot resolve prior concerns", and the regression test `test_round_one_response_cannot_resolve_concerns` (lines 100–107) locks it in.

B2: resolved. Verified at lines 180–185: a round-1 REVISE with empty `blocking_concerns` now raises "round-one REVISE responses require a blocking concern", guarded by `test_round_one_revise_requires_blocking_concern` (lines 110–116). Rounds 2–3 correctly remain unconstrained, since carried-over concerns make emptiness undecidable per-message there.

Verification performed: the workspace files match the rebuttal diff exactly, and the full suite passes — 18 passed in 0.04s (`uv run --no-sync pytest -q`, run without cache writes). The verification block in the rebuttal is accurate.

**Verdict:** APPROVE

## Post-Approval Follow-Up
S3 adopted at the operator's request. Added
`test_escalation_summary_accepts_ordered_unique_concerns`, which constructs a
valid summary with non-contiguous ordered IDs B1 and B3. Verification remains
green: Ruff passes and 19 tests pass.
