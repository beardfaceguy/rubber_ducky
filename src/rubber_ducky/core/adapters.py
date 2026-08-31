"""Provider-neutral LangChain adapters for review participants."""

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
) -> list[BaseMessage]:
    return [
        SystemMessage(
            content=(
                f"You are the {role} in review-protocol.md v1.3. "
                "Return only the requested structured protocol message. "
                "You have no tools and must not perform external writes."
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
    """Generate validated reviewer responses without binding tools."""

    model: StructuredOutputModel
    max_validation_attempts: int = 2

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
                    "Regenerate the entire response and correct every schema error. "
                    "Reviewer concern IDs must use B1, B2, ... and suggestion IDs "
                    "must use S1, S2, ... in monotonically increasing order. "
                    f"Validation error:\n{diagnostics[-1]}"
                )
            runnable = self.model.with_structured_output(response_schema)
            try:
                raw_response = runnable.invoke(
                    _participant_messages("reviewer", state, correction)
                )
                structured_response = response_schema.model_validate(raw_response)
                response = ReviewResponse.model_validate(
                    structured_response.model_dump()
                )
            except ValidationError as error:
                diagnostics.append(_validation_diagnostic(error))
                if attempt == self.max_validation_attempts:
                    raise
                continue
            apply_event(state, response)
            return ReviewGeneration(
                response=response,
                attempts=attempt,
                validation_errors=tuple(diagnostics),
            )
        raise AssertionError("validation-attempt loop did not return or raise")


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
