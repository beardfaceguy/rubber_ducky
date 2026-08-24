"""Provider-neutral LangChain adapters for review participants."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from agent_review.lifecycle import (
    InvalidTransition,
    ReviewState,
    ReviewStatus,
    apply_event,
)
from agent_review.models import (
    UNCHANGED_DIFF,
    Concern,
    Disposition,
    EscalationSummary,
    Position,
    ProtocolModel,
    Rebuttal,
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

    def review(self, state: ReviewState) -> ReviewResponse:
        if state.status is not ReviewStatus.AWAITING_REVIEW_RESPONSE:
            raise InvalidTransition(
                f"reviewer cannot act while status is {state.status.value}"
            )
        response_schema: type[BaseModel] = (
            _RoundOneReviewResponse if not state.responses else ReviewResponse
        )
        runnable = self.model.with_structured_output(response_schema)
        raw_response = runnable.invoke(_participant_messages("reviewer", state))
        structured_response = response_schema.model_validate(raw_response)
        response = ReviewResponse.model_validate(structured_response.model_dump())
        apply_event(state, response)
        return response


@dataclass(frozen=True)
class WorkerAdapter:
    """Generate validated rebuttals or escalation summaries without tools."""

    model: StructuredOutputModel

    def respond(
        self,
        state: ReviewState,
        *,
        revised_diff: str | None = None,
    ) -> Rebuttal | EscalationSummary:
        if state.status in {
            ReviewStatus.AWAITING_REBUTTAL,
            ReviewStatus.AWAITING_FINAL_POSITION,
        }:
            schema: type[Rebuttal | EscalationSummary] = Rebuttal
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
        if schema is Rebuttal:
            evidence_instruction = (
                "\n\nNo revised diff was supplied by the caller. Do not ACCEPT concerns "
                f"or invent code; use exactly {UNCHANGED_DIFF!r}."
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
        if isinstance(event, Rebuttal):
            accepted = any(
                response.disposition is Disposition.ACCEPT
                for response in event.blocking_responses
            )
            if revised_diff is None:
                if accepted:
                    raise InvalidTransition(
                        "ACCEPT requires a caller-supplied revised diff"
                    )
                if event.revised_diff != UNCHANGED_DIFF:
                    raise InvalidTransition("worker adapter cannot invent revised code")
            elif event.revised_diff != revised_diff.strip():
                raise InvalidTransition(
                    "model output does not match caller-supplied revised diff"
                )
        apply_event(state, event)
        return event
