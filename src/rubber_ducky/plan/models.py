"""Concrete plan review payload models and their durable state binding."""

from typing import Annotated

from pydantic import Field, model_validator

from rubber_ducky.core.lifecycle import ReviewState
from rubber_ducky.core.models import (
    UNCHANGED,
    NonEmptyText,
    ProtocolModel,
    RebuttalBase,
    ReviewRequestBase,
)


class PlanStep(ProtocolModel):
    """One ordered, individually acceptance-checkable step of a plan."""

    id: Annotated[str, Field(pattern=r"^P[1-9][0-9]*$")]
    description: NonEmptyText
    rationale: NonEmptyText | None = None
    acceptance: tuple[NonEmptyText, ...] = Field(default_factory=tuple)


class PlanDocument(ProtocolModel):
    """A structured plan a calling agent submits for review."""

    objective: NonEmptyText
    context: NonEmptyText | None = None
    steps: tuple[PlanStep, ...] = Field(min_length=1)
    acceptance_criteria: tuple[NonEmptyText, ...] = Field(min_length=1)
    risks: tuple[NonEmptyText, ...] = Field(default_factory=tuple)


def _render_plan(plan: PlanDocument) -> str:
    lines = [f"**Objective:** {plan.objective}"]
    if plan.context:
        lines.append(f"\n**Context:** {plan.context}")
    lines.append("\n**Steps:**")
    for step in plan.steps:
        lines.append(f"- {step.id}: {step.description}")
        if step.rationale:
            lines.append(f"  - Rationale: {step.rationale}")
        for check in step.acceptance:
            lines.append(f"  - Acceptance: {check}")
    lines.append("\n**Acceptance criteria:**")
    lines.extend(f"- {check}" for check in plan.acceptance_criteria)
    if plan.risks:
        lines.append("\n**Risks:**")
        lines.extend(f"- {risk}" for risk in plan.risks)
    return "\n".join(lines)


class PlanReviewRequest(ReviewRequestBase):
    """The initial plan review request carrying the proposed plan."""

    plan: PlanDocument

    def payload_heading(self) -> str:
        return "Proposed Plan"

    def payload_text(self) -> str:
        return _render_plan(self.plan)

    def artifact_suffix(self) -> str:
        return "md"


class PlanRebuttal(RebuttalBase):
    """A plan review worker response carrying an optional revised plan."""

    revised_plan: PlanDocument | None = None

    @model_validator(mode="after")
    def accepted_concerns_include_revised_plan(self) -> "PlanRebuttal":
        if self.accepts_any_concern() and self.revised_plan is None:
            raise ValueError("accepted concerns require an actual revised plan")
        return self

    def revised_heading(self) -> str:
        return "Revised Plan"

    def revised_text(self) -> str:
        if self.revised_plan is None:
            return UNCHANGED
        return _render_plan(self.revised_plan)

    def revised_artifact_suffix(self) -> str:
        return "md"

    def is_unchanged(self) -> bool:
        return self.revised_plan is None


class PlanReviewState(ReviewState):
    """Durable review state bound to the plan review payload types."""

    request_model = PlanReviewRequest
    rebuttal_model = PlanRebuttal


# Domain payload/state types the checkpoint allowlist must trust in addition
# to the shared core types.
PLAN_CHECKPOINT_TYPES: tuple[type, ...] = (
    PlanReviewRequest,
    PlanRebuttal,
    PlanReviewState,
    PlanDocument,
    PlanStep,
)
