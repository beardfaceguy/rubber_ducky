from rubber_ducky.core.checkpointing import (
    review_checkpoint_serializer,
    review_checkpoint_types,
)
from rubber_ducky.core.lifecycle import start_review
from rubber_ducky.plan.models import (
    PLAN_CHECKPOINT_TYPES,
    PlanDocument,
    PlanReviewRequest,
    PlanReviewState,
    PlanStep,
)


def _request() -> PlanReviewRequest:
    return PlanReviewRequest(
        task_id="AR-8",
        title="Round-trip the plan domain",
        proposed_solution="Register concrete plan payloads explicitly.",
        plan=PlanDocument(
            objective="Persist plan review state.",
            steps=(PlanStep(id="P1", description="Serialize the plan."),),
            acceptance_criteria=("round-trip is lossless",),
        ),
    )


def test_plan_domain_types_extend_the_trusted_allowlist() -> None:
    trusted = set(review_checkpoint_types(PLAN_CHECKPOINT_TYPES))

    assert set(PLAN_CHECKPOINT_TYPES) <= trusted


def test_plan_review_state_round_trips_through_the_plan_serializer() -> None:
    serializer = review_checkpoint_serializer(PLAN_CHECKPOINT_TYPES)
    original = start_review(_request(), state_cls=PlanReviewState)

    restored = serializer.loads_typed(serializer.dumps_typed(original))

    assert restored == original
    assert isinstance(restored, PlanReviewState)
    assert isinstance(restored.request, PlanReviewRequest)
    assert restored.request.plan.steps[0].id == "P1"
