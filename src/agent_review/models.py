"""Validated value objects for review-protocol.md v1.3."""

from enum import StrEnum
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RoundNumber = Annotated[int, Field(ge=1, le=3)]
UNCHANGED_DIFF = "Unchanged — see Review Request."


def _numbers_are_increasing(ids: list[str]) -> bool:
    numbers = [int(identifier[1:]) for identifier in ids]
    return all(left < right for left, right in pairwise(numbers))


class ProtocolModel(BaseModel):
    """Base configuration shared by protocol values."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )


class ConcernKind(StrEnum):
    BLOCKING = "blocking"
    SUGGESTION = "suggestion"


class Position(StrEnum):
    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    PARTIAL = "PARTIAL"


class Verdict(StrEnum):
    APPROVE = "APPROVE"
    REVISE = "REVISE"
    ESCALATE = "ESCALATE"


class Disposition(StrEnum):
    ACCEPT = "ACCEPT"
    DISPUTE = "DISPUTE"
    CLARIFY = "CLARIFY"


class RebuttalRequest(StrEnum):
    RE_REVIEW = "RE-REVIEW"
    WITHDRAWN = "WITHDRAWN"
    FINAL_POSITION = "FINAL-POSITION"


class Concern(ProtocolModel):
    """A reviewer-owned blocking concern or non-blocking suggestion."""

    id: Annotated[str, Field(pattern=r"^[BS][1-9][0-9]*$")]
    kind: ConcernKind
    text: NonEmptyText

    @model_validator(mode="after")
    def id_prefix_matches_kind(self) -> "Concern":
        expected_prefix = "B" if self.kind is ConcernKind.BLOCKING else "S"
        if not self.id.startswith(expected_prefix):
            raise ValueError(
                f"{self.kind.value} concern IDs must start with {expected_prefix}"
            )
        return self


class PriorPointResponse(ProtocolModel):
    """A reviewer response to a worker-owned rebuttal point."""

    point_id: Annotated[str, Field(pattern=r"^R[1-9][0-9]*$")]
    response: NonEmptyText


class BlockingConcernResponse(ProtocolModel):
    """A worker's disposition and reasoning for one blocking concern."""

    concern_id: Annotated[str, Field(pattern=r"^B[1-9][0-9]*$")]
    disposition: Disposition
    reason: NonEmptyText


class RebuttalPoint(ProtocolModel):
    """A worker-owned point introduced in a rebuttal."""

    id: Annotated[str, Field(pattern=r"^R[1-9][0-9]*$")]
    text: NonEmptyText


class Rebuttal(ProtocolModel):
    """A worker response to a REVISE verdict."""

    round: RoundNumber
    position: Position
    blocking_responses: tuple[BlockingConcernResponse, ...] = Field(min_length=1)
    revised_diff: NonEmptyText
    new_points: tuple[RebuttalPoint, ...] = Field(default_factory=tuple)
    requesting: RebuttalRequest

    @model_validator(mode="after")
    def accepted_concerns_include_revised_diff(self) -> "Rebuttal":
        blocking_ids = [response.concern_id for response in self.blocking_responses]
        point_ids = [point.id for point in self.new_points]
        if len(blocking_ids) != len(set(blocking_ids)):
            raise ValueError("each blocking concern may be answered only once")
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("rebuttal point IDs must be unique")
        if not _numbers_are_increasing(blocking_ids) or not _numbers_are_increasing(
            point_ids
        ):
            raise ValueError("rebuttal IDs must be in monotonically increasing order")
        accepted = any(
            response.disposition is Disposition.ACCEPT
            for response in self.blocking_responses
        )
        if accepted and self.revised_diff == UNCHANGED_DIFF:
            raise ValueError("accepted concerns require an actual revised diff")
        if self.round == 3 and self.requesting is RebuttalRequest.RE_REVIEW:
            raise ValueError("round-three rebuttals cannot request another review")
        if self.round < 3 and self.requesting is RebuttalRequest.FINAL_POSITION:
            raise ValueError("FINAL-POSITION is valid only in round three")
        return self


class ReviewResponse(ProtocolModel):
    """A reviewer's response for one of the three allowed rounds."""

    round: RoundNumber
    position: Position
    blocking_concerns: tuple[Concern, ...] = Field(default_factory=tuple)
    suggestions: tuple[Concern, ...] = Field(default_factory=tuple)
    resolved_concern_ids: tuple[
        Annotated[str, Field(pattern=r"^B[1-9][0-9]*$")], ...
    ] = Field(default_factory=tuple)
    prior_point_responses: tuple[PriorPointResponse, ...] = Field(default_factory=tuple)
    verdict: Verdict

    @model_validator(mode="after")
    def verdict_matches_concerns(self) -> "ReviewResponse":
        if any(
            concern.kind is not ConcernKind.BLOCKING
            for concern in self.blocking_concerns
        ):
            raise ValueError("blocking_concerns may contain only blocking concerns")
        if any(
            concern.kind is not ConcernKind.SUGGESTION for concern in self.suggestions
        ):
            raise ValueError("suggestions may contain only suggestions")
        if self.round == 1 and self.prior_point_responses:
            raise ValueError("round-one responses cannot address prior rebuttal points")
        if self.round == 1 and self.resolved_concern_ids:
            raise ValueError("round-one responses cannot resolve prior concerns")
        prior_point_ids = [response.point_id for response in self.prior_point_responses]
        if len(prior_point_ids) != len(set(prior_point_ids)):
            raise ValueError("each rebuttal point may be answered only once")
        if not _numbers_are_increasing(prior_point_ids):
            raise ValueError(
                "rebuttal point IDs must be in monotonically increasing order"
            )
        ids = [
            *(concern.id for concern in self.blocking_concerns),
            *(concern.id for concern in self.suggestions),
            *self.resolved_concern_ids,
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("concern IDs must be unique within a response")
        for concern_ids in (
            [concern.id for concern in self.blocking_concerns],
            [concern.id for concern in self.suggestions],
            list(self.resolved_concern_ids),
        ):
            if not _numbers_are_increasing(concern_ids):
                raise ValueError(
                    "concern IDs must be in monotonically increasing order"
                )
        if self.verdict is Verdict.APPROVE and self.blocking_concerns:
            raise ValueError("APPROVE responses cannot raise blocking concerns")
        if (
            self.round == 1
            and self.verdict is Verdict.REVISE
            and not self.blocking_concerns
        ):
            raise ValueError("round-one REVISE responses require a blocking concern")
        return self


class EscalationConcern(ProtocolModel):
    """An unresolved blocking concern included in an escalation."""

    concern_id: Annotated[str, Field(pattern=r"^B[1-9][0-9]*$")]
    status: NonEmptyText


class EscalationSummary(ProtocolModel):
    """The final positions and decision requested from the operator."""

    unresolved_blocking_concerns: tuple[EscalationConcern, ...] = Field(min_length=1)
    worker_position: NonEmptyText
    reviewer_position: NonEmptyText
    decision_needed: NonEmptyText

    @model_validator(mode="after")
    def concern_ids_are_unique_and_ordered(self) -> "EscalationSummary":
        concern_ids = [
            concern.concern_id for concern in self.unresolved_blocking_concerns
        ]
        if len(concern_ids) != len(set(concern_ids)):
            raise ValueError("escalation concern IDs must be unique")
        if not _numbers_are_increasing(concern_ids):
            raise ValueError(
                "escalation concern IDs must be in monotonically increasing order"
            )
        return self


class ReviewRequest(ProtocolModel):
    """The initial request that starts review round one."""

    protocol_version: Literal["1.3"] = "1.3"
    round: Literal[1] = 1
    task_id: NonEmptyText
    title: NonEmptyText
    proposed_solution: NonEmptyText
    relevant_diff: NonEmptyText
    known_concerns: tuple[NonEmptyText, ...] = Field(default_factory=tuple)
    questions: tuple[NonEmptyText, ...] = Field(default_factory=tuple)
