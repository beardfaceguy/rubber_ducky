"""Plan-review binding of the durable review service."""

from typing import ClassVar

from rubber_ducky.core.lifecycle import ReviewState
from rubber_ducky.core.models import RebuttalBase, ReviewRequestBase
from rubber_ducky.core.service import ReviewService
from rubber_ducky.plan.models import (
    PLAN_CHECKPOINT_TYPES,
    PlanRebuttal,
    PlanReviewRequest,
    PlanReviewState,
)


class PlanReviewService(ReviewService):
    """Durable review service bound to the plan-review payload domain."""

    request_model: ClassVar[type[ReviewRequestBase]] = PlanReviewRequest
    rebuttal_model: ClassVar[type[RebuttalBase]] = PlanRebuttal
    state_model: ClassVar[type[ReviewState]] = PlanReviewState
    checkpoint_types: ClassVar[tuple[type, ...]] = PLAN_CHECKPOINT_TYPES
