"""Concrete code-review payload models and their durable state binding."""

from pydantic import model_validator

from rubber_ducky.core.lifecycle import ReviewState
from rubber_ducky.core.models import (
    UNCHANGED,
    NonEmptyText,
    RebuttalBase,
    ReviewRequestBase,
)

# Domain-facing alias for the shared unchanged-payload sentinel.
UNCHANGED_DIFF = UNCHANGED


class ReviewRequest(ReviewRequestBase):
    """The initial code-review request carrying the reviewed diff."""

    relevant_diff: NonEmptyText

    def payload_heading(self) -> str:
        return "Relevant Code / Diff"

    def payload_text(self) -> str:
        return self.relevant_diff

    def artifact_suffix(self) -> str:
        return "diff"


class Rebuttal(RebuttalBase):
    """A code-review worker response carrying a revised diff."""

    revised_diff: NonEmptyText

    @model_validator(mode="after")
    def accepted_concerns_include_revised_diff(self) -> "Rebuttal":
        if self.accepts_any_concern() and self.revised_diff == UNCHANGED_DIFF:
            raise ValueError("accepted concerns require an actual revised diff")
        return self

    def revised_heading(self) -> str:
        return "Revised Code / Diff"

    def revised_text(self) -> str:
        return self.revised_diff

    def revised_artifact_suffix(self) -> str:
        return "diff"

    def is_unchanged(self) -> bool:
        return self.revised_diff == UNCHANGED_DIFF


class CodeReviewState(ReviewState):
    """Durable review state bound to the code-review payload types.

    Rebinding the ``request_model``/``rebuttal_model`` seam lets checkpoint
    reconstruction restore concrete code payloads (not the abstract base)
    when nested models arrive as plain dicts.
    """

    request_model = ReviewRequest
    rebuttal_model = Rebuttal


# Domain payload/state types the checkpoint allowlist must trust in addition
# to the shared core types.
CODE_CHECKPOINT_TYPES: tuple[type, ...] = (
    ReviewRequest,
    Rebuttal,
    CodeReviewState,
)
