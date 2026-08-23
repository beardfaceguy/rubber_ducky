"""LangGraph orchestration around the deterministic review reducer."""

from typing import Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from agent_review.checkpointing import in_memory_review_checkpointer
from agent_review.lifecycle import (
    InvalidTransition,
    ReviewState,
    ReviewStatus,
    apply_event,
    start_review,
)
from agent_review.models import ReviewRequest


class ReviewWorkflowState(TypedDict, total=False):
    """Checkpointed graph state for one review conversation."""

    request: ReviewRequest
    review: ReviewState


_TERMINAL_STATUSES = {
    ReviewStatus.APPROVED,
    ReviewStatus.WITHDRAWN,
    ReviewStatus.ESCALATED,
}

_EXPECTED_EVENT_TYPES = {
    ReviewStatus.AWAITING_REVIEW_RESPONSE: "review_response",
    ReviewStatus.AWAITING_REBUTTAL: "rebuttal",
    ReviewStatus.AWAITING_FINAL_POSITION: "rebuttal",
    ReviewStatus.AWAITING_ESCALATION_SUMMARY: "escalation_summary",
}


def _initialize(state: ReviewWorkflowState) -> ReviewWorkflowState:
    return {"review": start_review(state["request"])}


def _wait_for_event(state: ReviewWorkflowState) -> ReviewWorkflowState:
    review = state["review"]
    expected_round = (
        len(review.responses) + 1
        if review.status is ReviewStatus.AWAITING_REVIEW_RESPONSE
        else len(review.responses)
    )
    payload: dict[str, str | int] = {
        "status": review.status.value,
        "event_type": _EXPECTED_EVENT_TYPES[review.status],
    }
    if review.status is not ReviewStatus.AWAITING_ESCALATION_SUMMARY:
        payload["round"] = expected_round
    validation_error: str | None = None
    while True:
        event = interrupt(
            payload
            if validation_error is None
            else {
                **payload,
                "error": validation_error,
            }
        )
        try:
            return {"review": apply_event(review, event)}
        except InvalidTransition as error:
            validation_error = str(error)


def _route_after_event(state: ReviewWorkflowState) -> Literal["wait", "end"]:
    return "end" if state["review"].status in _TERMINAL_STATUSES else "wait"


def build_review_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Compile the review workflow with an in-memory saver by default."""

    workflow = StateGraph(ReviewWorkflowState)
    workflow.add_node("initialize", _initialize)
    workflow.add_node("wait_for_event", _wait_for_event)
    workflow.add_edge(START, "initialize")
    workflow.add_edge("initialize", "wait_for_event")
    workflow.add_conditional_edges(
        "wait_for_event",
        _route_after_event,
        {
            "wait": "wait_for_event",
            "end": END,
        },
    )
    if checkpointer is None:
        checkpointer = in_memory_review_checkpointer()
    return workflow.compile(
        checkpointer=checkpointer,
        name="agent-review",
    )
