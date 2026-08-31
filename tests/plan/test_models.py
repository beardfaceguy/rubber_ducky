import pytest
from pydantic import ValidationError

from rubber_ducky.core.models import (
    UNCHANGED,
    BlockingConcernResponse,
    Disposition,
    Position,
    RebuttalRequest,
)
from rubber_ducky.plan.models import (
    PlanDocument,
    PlanRebuttal,
    PlanReviewRequest,
    PlanStep,
)


def make_plan() -> PlanDocument:
    return PlanDocument(
        objective="Ship durable plan review.",
        context="Reuse the shared protocol engine.",
        steps=(
            PlanStep(
                id="P1",
                description="Define the plan document schema.",
                rationale="Callers need a stable structure.",
                acceptance=("PlanDocument validates a minimal plan.",),
            ),
        ),
        acceptance_criteria=("tests/plan green",),
    )


def make_request() -> PlanReviewRequest:
    return PlanReviewRequest(
        task_id="AR-8",
        title="Add plan review domain",
        proposed_solution="Mirror the code domain for plans.",
        plan=make_plan(),
    )


def test_plan_request_renders_payload_and_reports_markdown_suffix() -> None:
    request = make_request()

    assert request.payload_heading() == "Proposed Plan"
    assert request.artifact_suffix() == "md"
    text = request.payload_text()
    assert "Ship durable plan review." in text
    assert "P1" in text
    assert "Define the plan document schema." in text
    assert "tests/plan green" in text


def test_plan_document_requires_at_least_one_step() -> None:
    with pytest.raises(ValidationError):
        PlanDocument(
            objective="Empty plan.",
            steps=(),
            acceptance_criteria=("something",),
        )


def _blocking_response(disposition: Disposition) -> BlockingConcernResponse:
    return BlockingConcernResponse(
        concern_id="B1",
        disposition=disposition,
        reason="Addressed as noted.",
    )


def test_unchanged_plan_rebuttal_reports_sentinel_text() -> None:
    rebuttal = PlanRebuttal(
        round=1,
        position=Position.DISAGREE,
        blocking_responses=(_blocking_response(Disposition.DISPUTE),),
        requesting=RebuttalRequest.RE_REVIEW,
        revised_plan=None,
    )

    assert rebuttal.is_unchanged() is True
    assert rebuttal.revised_text() == UNCHANGED
    assert rebuttal.revised_artifact_suffix() == "md"
    assert rebuttal.revised_heading() == "Revised Plan"


def test_changed_plan_rebuttal_renders_revised_plan() -> None:
    rebuttal = PlanRebuttal(
        round=1,
        position=Position.AGREE,
        blocking_responses=(_blocking_response(Disposition.ACCEPT),),
        requesting=RebuttalRequest.RE_REVIEW,
        revised_plan=make_plan(),
    )

    assert rebuttal.is_unchanged() is False
    assert "Ship durable plan review." in rebuttal.revised_text()


def test_accepted_concern_requires_a_revised_plan() -> None:
    with pytest.raises(ValidationError):
        PlanRebuttal(
            round=1,
            position=Position.AGREE,
            blocking_responses=(_blocking_response(Disposition.ACCEPT),),
            requesting=RebuttalRequest.RE_REVIEW,
            revised_plan=None,
        )
