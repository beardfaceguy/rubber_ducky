from pathlib import Path

from rubber_ducky.core.lifecycle import ReviewStatus
from rubber_ducky.core.models import Position, ReviewResponse, Verdict
from rubber_ducky.plan.models import (
    PlanDocument,
    PlanReviewRequest,
    PlanReviewState,
    PlanStep,
)
from rubber_ducky.plan.service import PlanReviewService


def make_request() -> PlanReviewRequest:
    return PlanReviewRequest(
        task_id="AR-8",
        title="Add plan review domain",
        proposed_solution="Mirror the code domain for plans.",
        plan=PlanDocument(
            objective="Ship durable plan review.",
            steps=(PlanStep(id="P1", description="Persist plan state."),),
            acceptance_criteria=("tests/plan green",),
        ),
    )


def test_plan_review_starts_and_approves_through_durable_service(
    tmp_path: Path,
) -> None:
    service = PlanReviewService(tmp_path)

    started = service.start("plan-1", "add-plan-review", make_request())
    assert started.status is ReviewStatus.AWAITING_REVIEW_RESPONSE

    approval = ReviewResponse(
        round=1,
        position=Position.AGREE,
        verdict=Verdict.APPROVE,
    )
    completed = service.submit("plan-1", "event-1", approval)
    assert completed.status is ReviewStatus.APPROVED

    reopened = PlanReviewService(tmp_path).status("plan-1")
    assert reopened.status is ReviewStatus.APPROVED
    assert isinstance(reopened, PlanReviewState)
    assert isinstance(reopened.request, PlanReviewRequest)
    assert reopened.request.plan.objective == "Ship durable plan review."
