"""Framework-independent state transitions for the review protocol."""

from collections.abc import Iterable
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, field_validator

from rubber_ducky.core.models import (
    Concern,
    EscalationSummary,
    RebuttalBase,
    RebuttalRequest,
    ReviewRequestBase,
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


_EXPECTED_EVENT_TYPES = {
    ReviewStatus.AWAITING_REVIEW_RESPONSE: "review_response",
    ReviewStatus.AWAITING_REBUTTAL: "rebuttal",
    ReviewStatus.AWAITING_FINAL_POSITION: "rebuttal",
    ReviewStatus.AWAITING_ESCALATION_SUMMARY: "escalation_summary",
}


def expected_event_type(status: ReviewStatus) -> str | None:
    """Return the protocol event expected for a non-terminal status."""

    return _EXPECTED_EVENT_TYPES.get(status)


class ReviewState(BaseModel):
    """Immutable aggregate state for one review conversation.

    The ``request_model``/``rebuttal_model`` class vars bind the concrete
    domain payload types. They exist so that when a checkpoint reloads this
    state (its nested models flattened to plain dicts), the polymorphic
    ``request``/``rebuttals`` fields reconstruct as the concrete domain type
    rather than the abstract base. Domain packages subclass to rebind them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_model: ClassVar[type[ReviewRequestBase]] = ReviewRequestBase
    rebuttal_model: ClassVar[type[RebuttalBase]] = RebuttalBase

    status: ReviewStatus
    request: SerializeAsAny[ReviewRequestBase]
    responses: tuple[ReviewResponse, ...] = Field(default_factory=tuple)
    rebuttals: tuple[SerializeAsAny[RebuttalBase], ...] = Field(default_factory=tuple)
    open_blocking_concerns: tuple[Concern, ...] = Field(default_factory=tuple)
    suggestions: tuple[Concern, ...] = Field(default_factory=tuple)
    escalation_summary: EscalationSummary | None = None

    @field_validator("request", mode="before")
    @classmethod
    def _coerce_request(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return cls.request_model.model_validate(value)
        return value

    @field_validator("rebuttals", mode="before")
    @classmethod
    def _coerce_rebuttals(cls, value: Any) -> Any:
        if value is None:
            return value
        return tuple(
            cls.rebuttal_model.model_validate(item) if isinstance(item, dict) else item
            for item in value
        )


class InvalidTransition(ValueError):
    """Raised when a message cannot follow the current review state."""


ReviewEvent = ReviewResponse | RebuttalBase | EscalationSummary


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


def start_review(
    request: ReviewRequestBase,
    *,
    state_cls: type[ReviewState] = ReviewState,
) -> ReviewState:
    """Start a conversation waiting for its first reviewer response.

    ``state_cls`` selects the domain's ``ReviewState`` subclass so that its
    concrete-payload class-var seam governs later checkpoint reconstruction.
    """

    return state_cls(
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
                "review response must address every point from the prior rebuttal: "
                f"expected {expected_point_ids}, got {addressed_point_ids}"
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
        next_status = (
            ReviewStatus.AWAITING_FINAL_POSITION
            if response.round == 3
            else ReviewStatus.AWAITING_ESCALATION_SUMMARY
        )

    return state.model_copy(
        update={
            "status": next_status,
            "responses": (*state.responses, response),
            "open_blocking_concerns": open_concerns,
            "suggestions": (*state.suggestions, *response.suggestions),
        }
    )


def apply_rebuttal(state: ReviewState, rebuttal: RebuttalBase) -> ReviewState:
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
        raise InvalidTransition(
            "rebuttal must address every open blocking concern: "
            f"expected {expected_ids}, got {response_ids}"
        )

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
            "escalation summary must include every open blocking concern: "
            f"expected {open_ids}, got {summary_ids}"
        )

    return state.model_copy(
        update={
            "status": ReviewStatus.ESCALATED,
            "escalation_summary": summary,
        }
    )


def apply_event(state: ReviewState, event: ReviewEvent) -> ReviewState:
    """Dispatch one validated protocol event to its domain transition."""

    if isinstance(event, ReviewResponse):
        return apply_review_response(state, event)
    if isinstance(event, RebuttalBase):
        return apply_rebuttal(state, event)
    if isinstance(event, EscalationSummary):
        return finalize_escalation(state, event)
    raise InvalidTransition(f"unsupported review event type: {type(event).__name__}")


def replay_review(
    request: ReviewRequestBase,
    events: Iterable[ReviewEvent],
    *,
    state_cls: type[ReviewState] = ReviewState,
) -> ReviewState:
    """Rebuild canonical state by replaying validated protocol messages."""

    state = start_review(request, state_cls=state_cls)
    for index, event in enumerate(events, start=1):
        try:
            state = apply_event(state, event)
        except InvalidTransition as error:
            raise InvalidTransition(
                f"invalid event {index} ({type(event).__name__}): {error}"
            ) from error
    return state
