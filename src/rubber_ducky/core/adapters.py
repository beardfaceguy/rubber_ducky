"""Provider-neutral LangChain adapters for review participants."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field, ValidationError

from rubber_ducky.core.lifecycle import (
    InvalidTransition,
    ReviewState,
    ReviewStatus,
    apply_event,
)
from rubber_ducky.core.models import (
    UNCHANGED,
    Concern,
    Disposition,
    EscalationSummary,
    Position,
    ProtocolModel,
    RebuttalBase,
    ReviewResponse,
    Verdict,
)


class StructuredOutputModel(Protocol):
    """Minimal LangChain model contract required by participant adapters."""

    def with_structured_output(
        self,
        schema: type[BaseModel],
        **kwargs: Any,
    ) -> Runnable[Any, Any]: ...


class _RoundOneReviewResponse(ProtocolModel):
    """Reviewer output fields valid before any worker rebuttal exists."""

    round: Literal[1]
    position: Position
    blocking_concerns: tuple[Concern, ...] = Field(default_factory=tuple)
    suggestions: tuple[Concern, ...] = Field(default_factory=tuple)
    verdict: Verdict


@dataclass(frozen=True)
class ReviewGeneration:
    """Validated reviewer response plus bounded retry diagnostics."""

    response: ReviewResponse
    attempts: int
    validation_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class RejectedGeneration:
    """The last reviewer output rejected after every attempt was spent.

    ``response`` is the model output exactly as returned (or as validated,
    when only the lifecycle rejected it); it is never repaired.
    """

    response: object
    attempts: int
    validation_errors: tuple[str, ...]


RejectionHandler = Callable[[RejectedGeneration], None]


# Structured-field rules enforced by ``core/lifecycle.py``. The protocol
# document is named in the system message but not sent, so the reviewer only
# learns these rules from here.
_REVIEWER_FIELD_RULES = (
    "Structured-field rules (the review is rejected if any is broken):\n"
    "- blocking_concerns and suggestions contain ONLY concerns you are raising "
    "for the first time in this response. Their IDs continue the "
    "conversation-wide B and S sequences: after B1-B3 have been raised, the "
    "next new blocking concern is B4. Never list an earlier concern ID in "
    "blocking_concerns or suggestions again.\n"
    "- Every open blocking concern is carried forward automatically. It stays "
    "open unless its ID is in resolved_concern_ids. To keep an existing "
    "concern open (for example one the worker disputed and you still hold), "
    "leave its ID out of both resolved_concern_ids and blocking_concerns.\n"
    "- resolved_concern_ids may contain only currently open blocking concern "
    "IDs.\n"
    "- In round 2 and later, prior_point_responses must answer every R point "
    "of the most recent rebuttal, once each, in order.\n"
    "- In round 2 and later, prior_concern_responses answers the worker's "
    "disposition reasons from the most recent rebuttal, by concern ID, once "
    "each, in increasing ID order. Every concern the worker DISPUTEd or "
    "CLARIFYd that you keep open MUST have an entry explaining why the "
    "worker's reason does not resolve it. You may also answer other concerns "
    "the rebuttal addressed. An entry never resolves a concern; only "
    "resolved_concern_ids does.\n"
    "- APPROVE requires that no blocking concern stays open. REVISE and "
    "ESCALATE require at least one blocking concern to stay open."
)


def _require_answers_to_contested_concerns(
    state: ReviewState,
    response: ReviewResponse,
) -> None:
    """Require a reply to every DISPUTE/CLARIFY the reviewer keeps open.

    This is a generation-time rule only: the lifecycle keeps the field
    optional so journals written before the field existed still replay.
    """

    if response.round == 1 or not state.rebuttals:
        return
    resolved_ids = set(response.resolved_concern_ids)
    answered_ids = {reply.concern_id for reply in response.prior_concern_responses}
    unanswered_ids = tuple(
        disposition.concern_id
        for disposition in state.rebuttals[-1].blocking_responses
        if disposition.disposition is not Disposition.ACCEPT
        and disposition.concern_id not in resolved_ids
        and disposition.concern_id not in answered_ids
    )
    if unanswered_ids:
        raise InvalidTransition(
            "prior_concern_responses must answer every disputed or clarified "
            f"concern that stays open: missing {unanswered_ids}"
        )


def _validation_diagnostic(error: ValidationError) -> str:
    """Keep schema failure evidence without persisting model-provided values."""

    entries = []
    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        entries.append(f"{location}: {item['type']}: {item['msg']}")
    return "; ".join(entries)[:2000]


def _participant_messages(
    role: str,
    state: ReviewState,
    evidence_instruction: str = "",
    role_rules: str = "",
) -> list[BaseMessage]:
    return [
        SystemMessage(
            content=(
                f"You are the {role} in review-protocol.md v1.3. "
                "Return only the requested structured protocol message. "
                "You have no tools and must not perform external writes."
                + (f"\n\n{role_rules}" if role_rules else "")
            )
        ),
        HumanMessage(
            content=(
                "Produce the next protocol event for this canonical review state:\n"
                f"{state.model_dump_json(indent=2)}"
                f"{evidence_instruction}"
            )
        ),
    ]


@dataclass(frozen=True)
class ReviewerAdapter:
    """Generate validated reviewer responses without binding tools.

    A candidate must pass both the response schema and the lifecycle
    transition (``apply_event``) for the current state. Either failure is fed
    back to the model on the next attempt, within ``max_validation_attempts``.
    When every attempt fails, ``on_rejected`` receives the last rejected
    output before the final error is raised.
    """

    model: StructuredOutputModel
    max_validation_attempts: int = 2
    on_rejected: RejectionHandler | None = None

    def review(self, state: ReviewState) -> ReviewResponse:
        return self.review_with_diagnostics(state).response

    def review_with_diagnostics(self, state: ReviewState) -> ReviewGeneration:
        if state.status is not ReviewStatus.AWAITING_REVIEW_RESPONSE:
            raise InvalidTransition(
                f"reviewer cannot act while status is {state.status.value}"
            )
        if self.max_validation_attempts < 1:
            raise ValueError("max_validation_attempts must be positive")
        response_schema: type[BaseModel] = (
            _RoundOneReviewResponse if not state.responses else ReviewResponse
        )
        diagnostics: list[str] = []
        for attempt in range(1, self.max_validation_attempts + 1):
            correction = ""
            if diagnostics:
                correction = (
                    "\n\nThe previous structured response failed validation. "
                    "Regenerate the entire response and correct every error. "
                    "New concern IDs continue the conversation-wide B and S "
                    "sequences; follow the structured-field rules exactly. "
                    f"Validation error:\n{diagnostics[-1]}"
                )
            runnable = self.model.with_structured_output(response_schema)
            raw_response: object = None
            try:
                raw_response = runnable.invoke(
                    _participant_messages(
                        "reviewer",
                        state,
                        correction,
                        role_rules=_REVIEWER_FIELD_RULES,
                    )
                )
                structured_response = response_schema.model_validate(raw_response)
                response = ReviewResponse.model_validate(
                    structured_response.model_dump()
                )
            except ValidationError as error:
                diagnostics.append(_validation_diagnostic(error))
                if attempt == self.max_validation_attempts:
                    self._report_rejection(raw_response, attempt, diagnostics)
                    raise
                continue
            try:
                # Pure dry run: the returned state is discarded.
                apply_event(state, response)
                _require_answers_to_contested_concerns(state, response)
            except InvalidTransition as error:
                diagnostics.append(f"lifecycle: {type(error).__name__}: {error}"[:2000])
                if attempt == self.max_validation_attempts:
                    self._report_rejection(response, attempt, diagnostics)
                    raise
                continue
            return ReviewGeneration(
                response=response,
                attempts=attempt,
                validation_errors=tuple(diagnostics),
            )
        raise AssertionError("validation-attempt loop did not return or raise")

    def _report_rejection(
        self,
        response: object,
        attempts: int,
        diagnostics: list[str],
    ) -> None:
        if self.on_rejected is None:
            return
        self.on_rejected(
            RejectedGeneration(
                response=response,
                attempts=attempts,
                validation_errors=tuple(diagnostics),
            )
        )


@dataclass(frozen=True)
class WorkerAdapter:
    """Generate validated rebuttals or escalation summaries without tools.

    ``rebuttal_schema`` binds the domain's concrete rebuttal type, and
    ``unchanged_marker`` is the sentinel used when no revision is supplied.
    """

    model: StructuredOutputModel
    rebuttal_schema: type[RebuttalBase]
    unchanged_marker: str = UNCHANGED

    def respond(
        self,
        state: ReviewState,
        *,
        revised_diff: str | None = None,
    ) -> RebuttalBase | EscalationSummary:
        if state.status in {
            ReviewStatus.AWAITING_REBUTTAL,
            ReviewStatus.AWAITING_FINAL_POSITION,
        }:
            schema: type[RebuttalBase | EscalationSummary] = self.rebuttal_schema
        elif state.status is ReviewStatus.AWAITING_ESCALATION_SUMMARY:
            if revised_diff is not None:
                raise InvalidTransition(
                    "escalation summary does not accept a revised diff"
                )
            schema = EscalationSummary
        else:
            raise InvalidTransition(
                f"worker cannot act while status is {state.status.value}"
            )

        evidence_instruction = ""
        if schema is self.rebuttal_schema:
            evidence_instruction = (
                "\n\nNo revised diff was supplied by the caller. Do not ACCEPT concerns "
                f"or invent code; use exactly {self.unchanged_marker!r}."
                if revised_diff is None
                else (
                    "\n\nUse this caller-supplied revised diff exactly; do not alter or "
                    f"invent code:\n{revised_diff}"
                )
            )
        runnable = self.model.with_structured_output(schema)
        raw_response = runnable.invoke(
            _participant_messages("worker", state, evidence_instruction)
        )
        event = schema.model_validate(raw_response)
        if isinstance(event, RebuttalBase):
            if revised_diff is None:
                if event.accepts_any_concern():
                    raise InvalidTransition(
                        "ACCEPT requires a caller-supplied revised diff"
                    )
                if not event.is_unchanged():
                    raise InvalidTransition("worker adapter cannot invent revised code")
            elif event.revised_text() != revised_diff.strip():
                raise InvalidTransition(
                    "model output does not match caller-supplied revised diff"
                )
        apply_event(state, event)
        return event
