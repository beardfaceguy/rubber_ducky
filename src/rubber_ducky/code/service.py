"""Code-review binding of the durable review service."""

from typing import ClassVar

from rubber_ducky.code.models import (
    CODE_CHECKPOINT_TYPES,
    CodeReviewState,
    Rebuttal,
    ReviewRequest,
)
from rubber_ducky.core.lifecycle import ReviewState
from rubber_ducky.core.models import RebuttalBase, ReviewRequestBase
from rubber_ducky.core.service import ReviewService


class CodeReviewService(ReviewService):
    """Durable review service bound to the code-review payload domain."""

    request_model: ClassVar[type[ReviewRequestBase]] = ReviewRequest
    rebuttal_model: ClassVar[type[RebuttalBase]] = Rebuttal
    state_model: ClassVar[type[ReviewState]] = CodeReviewState
    checkpoint_types: ClassVar[tuple[type, ...]] = CODE_CHECKPOINT_TYPES
