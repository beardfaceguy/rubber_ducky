from typing import Any

import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from agent_review.adapters import ReviewerAdapter, WorkerAdapter
from agent_review.lifecycle import (
    InvalidTransition,
    apply_review_response,
    replay_review,
    start_review,
)
from agent_review.models import (
    BlockingConcernResponse,
    Concern,
    ConcernKind,
    Disposition,
    EscalationSummary,
    Rebuttal,
    RebuttalRequest,
    ReviewRequest,
    ReviewResponse,
)


class FakeStructuredModel:
    def __init__(self, response: object) -> None:
        self.response = response
        self.schemas: list[type] = []
        self.inputs: list[object] = []
        self.bind_tools_called = False

    def with_structured_output(self, schema: type) -> RunnableLambda:
        self.schemas.append(schema)
        return RunnableLambda(self._respond)

    def _respond(self, model_input: object) -> object:
        self.inputs.append(model_input)
        return self.response

    def bind_tools(self, *_args: Any, **_kwargs: Any) -> None:
        self.bind_tools_called = True
        raise AssertionError("reviewer tools must never be bound")


def make_request() -> ReviewRequest:
    return ReviewRequest(
        task_id="AR-5",
        title="Add model adapters",
        proposed_solution="Use structured output without tools.",
        relevant_diff="+adapter = ReviewerAdapter(model)",
    )


def test_reviewer_returns_validated_structured_response_without_tools() -> None:
    state = start_review(make_request())
    model = FakeStructuredModel(
        {
            "round": 1,
            "position": "AGREE",
            "verdict": "APPROVE",
        }
    )

    response = ReviewerAdapter(model).review(state)

    assert response == ReviewResponse(
        round=1,
        position="AGREE",
        verdict="APPROVE",
    )
    assert model.schemas == [ReviewResponse]
    assert not model.bind_tools_called
    assert make_request().relevant_diff in str(model.inputs[0])


def test_reviewer_rejects_wrong_status_before_model_call() -> None:
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position="PARTIAL",
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="Blocked."),
            ),
            verdict="REVISE",
        ),
    )
    model = FakeStructuredModel({})

    with pytest.raises(InvalidTransition, match="reviewer cannot act"):
        ReviewerAdapter(model).review(state)

    assert model.inputs == []


def test_reviewer_dry_run_rejects_lifecycle_invalid_output() -> None:
    state = start_review(make_request())
    model = FakeStructuredModel(
        {
            "round": 2,
            "position": "AGREE",
            "verdict": "APPROVE",
        }
    )

    with pytest.raises(InvalidTransition, match="expected review response round 1"):
        ReviewerAdapter(model).review(state)

    assert state.responses == ()


def test_reviewer_revalidates_malformed_model_output() -> None:
    model = FakeStructuredModel({"round": 1})

    with pytest.raises(ValidationError):
        ReviewerAdapter(model).review(start_review(make_request()))


def test_reviewer_revalidates_preconstructed_model_instance() -> None:
    invalid = ReviewResponse.model_construct(
        round=4,
        position="AGREE",
        verdict="APPROVE",
    )
    model = FakeStructuredModel(invalid)

    with pytest.raises(ValidationError):
        ReviewerAdapter(model).review(start_review(make_request()))


def test_worker_returns_validated_rebuttal_without_tools() -> None:
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position="PARTIAL",
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="Blocked."),
            ),
            verdict="REVISE",
        ),
    )
    model = FakeStructuredModel(
        {
            "round": 1,
            "position": "DISAGREE",
            "blocking_responses": [
                {
                    "concern_id": "B1",
                    "disposition": "DISPUTE",
                    "reason": "The validation already exists.",
                }
            ],
            "revised_diff": "Unchanged — see Review Request.",
            "requesting": "RE-REVIEW",
        }
    )

    rebuttal = WorkerAdapter(model).respond(state)

    assert isinstance(rebuttal, Rebuttal)
    assert model.schemas == [Rebuttal]
    assert not model.bind_tools_called


def test_worker_cannot_invent_diff_for_accepted_concern() -> None:
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position="PARTIAL",
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="Blocked."),
            ),
            verdict="REVISE",
        ),
    )
    model = FakeStructuredModel(
        {
            "round": 1,
            "position": "AGREE",
            "blocking_responses": [
                {
                    "concern_id": "B1",
                    "disposition": "ACCEPT",
                    "reason": "The concern is valid.",
                }
            ],
            "revised_diff": "+invented_fix = True",
            "requesting": "RE-REVIEW",
        }
    )

    with pytest.raises(InvalidTransition, match="caller-supplied revised diff"):
        WorkerAdapter(model).respond(state)


def test_worker_cannot_invent_diff_while_disputing_concern() -> None:
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position="PARTIAL",
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="Blocked."),
            ),
            verdict="REVISE",
        ),
    )
    model = FakeStructuredModel(
        {
            "round": 1,
            "position": "DISAGREE",
            "blocking_responses": [
                {
                    "concern_id": "B1",
                    "disposition": "DISPUTE",
                    "reason": "The concern does not apply.",
                }
            ],
            "revised_diff": "+invented_despite_dispute = True",
            "requesting": "RE-REVIEW",
        }
    )

    with pytest.raises(InvalidTransition, match="cannot invent revised code"):
        WorkerAdapter(model).respond(state)


def test_worker_accepts_only_exact_caller_supplied_diff() -> None:
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position="PARTIAL",
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="Blocked."),
            ),
            verdict="REVISE",
        ),
    )
    revised_diff = "+validated_fix = True"
    model = FakeStructuredModel(
        {
            "round": 1,
            "position": "AGREE",
            "blocking_responses": [
                {
                    "concern_id": "B1",
                    "disposition": "ACCEPT",
                    "reason": "The concern is valid.",
                }
            ],
            "revised_diff": revised_diff,
            "requesting": "RE-REVIEW",
        }
    )

    rebuttal = WorkerAdapter(model).respond(state, revised_diff=revised_diff)

    assert isinstance(rebuttal, Rebuttal)
    assert rebuttal.revised_diff == revised_diff
    assert revised_diff in str(model.inputs[0])


def test_worker_rejects_model_alteration_of_supplied_diff() -> None:
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position="PARTIAL",
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="Blocked."),
            ),
            verdict="REVISE",
        ),
    )
    model = FakeStructuredModel(
        {
            "round": 1,
            "position": "AGREE",
            "blocking_responses": [
                {
                    "concern_id": "B1",
                    "disposition": "ACCEPT",
                    "reason": "The concern is valid.",
                }
            ],
            "revised_diff": "+altered = True",
            "requesting": "RE-REVIEW",
        }
    )

    with pytest.raises(InvalidTransition, match="does not match"):
        WorkerAdapter(model).respond(state, revised_diff="+authoritative = True")


def test_worker_selects_escalation_summary_for_escalation_status() -> None:
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position="DISAGREE",
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="Deadlocked."),
            ),
            verdict="ESCALATE",
        ),
    )
    model = FakeStructuredModel(
        {
            "unresolved_blocking_concerns": [
                {
                    "concern_id": "B1",
                    "status": "Still disputed.",
                }
            ],
            "worker_position": "The implementation is safe.",
            "reviewer_position": "The blocker remains.",
            "decision_needed": "Should implementation continue?",
        }
    )

    summary = WorkerAdapter(model).respond(state)

    assert isinstance(summary, EscalationSummary)
    assert model.schemas == [EscalationSummary]


def test_worker_rejects_revised_diff_for_escalation_summary() -> None:
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position="DISAGREE",
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="Deadlocked."),
            ),
            verdict="ESCALATE",
        ),
    )
    model = FakeStructuredModel({})

    with pytest.raises(InvalidTransition, match="does not accept a revised diff"):
        WorkerAdapter(model).respond(state, revised_diff="+irrelevant = True")

    assert model.inputs == []


def test_worker_selects_rebuttal_for_final_position_status() -> None:
    blocker = Concern(id="B1", kind=ConcernKind.BLOCKING, text="Still blocked.")
    state = replay_review(
        make_request(),
        (
            ReviewResponse(
                round=1,
                position="DISAGREE",
                blocking_concerns=(blocker,),
                verdict="REVISE",
            ),
            Rebuttal(
                round=1,
                position="DISAGREE",
                blocking_responses=(
                    BlockingConcernResponse(
                        concern_id="B1",
                        disposition=Disposition.DISPUTE,
                        reason="Round one dispute.",
                    ),
                ),
                revised_diff="Unchanged — see Review Request.",
                requesting=RebuttalRequest.RE_REVIEW,
            ),
            ReviewResponse(
                round=2,
                position="DISAGREE",
                verdict="REVISE",
            ),
            Rebuttal(
                round=2,
                position="DISAGREE",
                blocking_responses=(
                    BlockingConcernResponse(
                        concern_id="B1",
                        disposition=Disposition.DISPUTE,
                        reason="Round two dispute.",
                    ),
                ),
                revised_diff="Unchanged — see Review Request.",
                requesting=RebuttalRequest.RE_REVIEW,
            ),
            ReviewResponse(
                round=3,
                position="DISAGREE",
                verdict="REVISE",
            ),
        ),
    )
    model = FakeStructuredModel(
        {
            "round": 3,
            "position": "DISAGREE",
            "blocking_responses": [
                {
                    "concern_id": "B1",
                    "disposition": "DISPUTE",
                    "reason": "Final position.",
                }
            ],
            "revised_diff": "Unchanged — see Review Request.",
            "requesting": "FINAL-POSITION",
        }
    )

    final_position = WorkerAdapter(model).respond(state)

    assert isinstance(final_position, Rebuttal)
    assert model.schemas == [Rebuttal]


def test_worker_rejects_wrong_status_before_model_call() -> None:
    model = FakeStructuredModel({})

    with pytest.raises(InvalidTransition, match="worker cannot act"):
        WorkerAdapter(model).respond(start_review(make_request()))

    assert model.inputs == []
