"""Checkpoint factories with explicit, extensible type trust."""

from collections.abc import Iterable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from agent_review.lifecycle import ReviewState, ReviewStatus
from agent_review.models import (
    BlockingConcernResponse,
    Concern,
    ConcernKind,
    Disposition,
    EscalationConcern,
    EscalationSummary,
    Position,
    PriorPointResponse,
    ProtocolModel,
    Rebuttal,
    RebuttalPoint,
    RebuttalRequest,
    ReviewRequest,
    ReviewResponse,
    Verdict,
)

_REVIEW_CHECKPOINT_TYPES: tuple[type, ...] = (
    ReviewRequest,
    ReviewResponse,
    Rebuttal,
    EscalationSummary,
    Concern,
    PriorPointResponse,
    BlockingConcernResponse,
    RebuttalPoint,
    EscalationConcern,
    Position,
    Verdict,
    ConcernKind,
    Disposition,
    RebuttalRequest,
    ReviewState,
    ReviewStatus,
    ProtocolModel,
)


def review_checkpoint_types(
    additional_types: Iterable[type] = (),
) -> tuple[type, ...]:
    """Return the canonical trusted type set plus explicit extensions."""

    return tuple(dict.fromkeys((*_REVIEW_CHECKPOINT_TYPES, *additional_types)))


def review_checkpoint_serializer(
    additional_types: Iterable[type] = (),
) -> JsonPlusSerializer:
    """Create a strict serializer for review state plus trusted extensions."""

    return JsonPlusSerializer(
        allowed_msgpack_modules=review_checkpoint_types(additional_types)
    )


def in_memory_review_checkpointer(
    additional_types: Iterable[type] = (),
) -> InMemorySaver:
    """Create an in-memory saver using the shared review serializer."""

    return InMemorySaver(serde=review_checkpoint_serializer(additional_types))
