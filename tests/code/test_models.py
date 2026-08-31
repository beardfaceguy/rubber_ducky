import pytest
from pydantic import ValidationError

from rubber_ducky.code.models import Rebuttal, ReviewRequest
from rubber_ducky.core.models import (
    BlockingConcernResponse,
    Concern,
    ConcernKind,
    Disposition,
    EscalationConcern,
    EscalationSummary,
    Position,
    PriorPointResponse,
    RebuttalPoint,
    RebuttalRequest,
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


def test_round_one_response_cannot_resolve_concerns() -> None:
    with pytest.raises(ValidationError):
        ReviewResponse(
            round=1,
            position=Position.AGREE,
            resolved_concern_ids=("B1",),
            verdict=Verdict.APPROVE,
        )


def test_round_one_revise_requires_blocking_concern() -> None:
    with pytest.raises(ValidationError):
        ReviewResponse(
            round=1,
            position=Position.DISAGREE,
            verdict=Verdict.REVISE,
        )


def test_round_two_accepts_complete_valid_response() -> None:
    response = ReviewResponse(
        round=2,
        position=Position.PARTIAL,
        blocking_concerns=(
            Concern(id="B2", kind=ConcernKind.BLOCKING, text="A new blocker."),
        ),
        suggestions=(
            Concern(id="S1", kind=ConcernKind.SUGGESTION, text="Optional cleanup."),
        ),
        resolved_concern_ids=("B1",),
        prior_point_responses=(
            PriorPointResponse(point_id="R1", response="The evidence resolves B1."),
        ),
        verdict=Verdict.REVISE,
    )

    assert response.resolved_concern_ids == ("B1",)


def test_escalation_summary_requires_blocking_concern_ids() -> None:
    with pytest.raises(ValidationError):
        EscalationSummary(
            unresolved_blocking_concerns=(
                EscalationConcern(concern_id="R1", status="Still disputed."),
            ),
            worker_position="The implementation is safe.",
            reviewer_position="The race remains.",
            decision_needed="Should the implementation proceed?",
        )


def test_escalation_summary_accepts_ordered_unique_concerns() -> None:
    summary = EscalationSummary(
        unresolved_blocking_concerns=(
            EscalationConcern(concern_id="B1", status="Revision disputed."),
            EscalationConcern(concern_id="B3", status="Evidence remains incomplete."),
        ),
        worker_position="The implementation is safe.",
        reviewer_position="The remaining risks still block approval.",
        decision_needed="Should the implementation proceed?",
    )

    assert [concern.concern_id for concern in summary.unresolved_blocking_concerns] == [
        "B1",
        "B3",
    ]


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
