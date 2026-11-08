from typing import Any

import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from rubber_ducky.core.adapters import (
    RejectedGeneration,
    ReviewerAdapter,
    WorkerAdapter,
)
from rubber_ducky.core.lifecycle import (
    InvalidTransition,
    apply_review_response,
    replay_review,
    start_review,
)
from rubber_ducky.code.models import Rebuttal, ReviewRequest
from rubber_ducky.core.models import (
    BlockingConcernResponse,
    Concern,
    ConcernKind,
    Disposition,
    EscalationSummary,
    RebuttalPoint,
    RebuttalRequest,
    ReviewResponse,
)


class FakeStructuredModel:
    def __init__(self, response: object) -> None:
        self.responses = list(response) if isinstance(response, list) else [response]
        self.schemas: list[type] = []
        self.inputs: list[object] = []
        self.bind_tools_called = False

    def with_structured_output(self, schema: type, **_kwargs: Any) -> RunnableLambda:
        self.schemas.append(schema)
        return RunnableLambda(self._respond)

    def _respond(self, model_input: object) -> object:
        self.inputs.append(model_input)
        return self.responses.pop(0)

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
    schema_properties = model.schemas[0].model_json_schema()["properties"]
    assert "prior_point_responses" not in schema_properties
    assert "resolved_concern_ids" not in schema_properties
    assert not model.bind_tools_called
    assert make_request().relevant_diff in str(model.inputs[0])


def test_reviewer_retries_one_invalid_structured_response_with_diagnostics() -> None:
    model = FakeStructuredModel(
        [
            {
                "round": 1,
                "position": "AGREE",
                "suggestions": [
                    {
                        "id": "api/app/tasks.py finding",
                        "kind": "suggestion",
                        "text": "Malformed model-owned ID.",
                    }
                ],
                "verdict": "APPROVE",
            },
            {
                "round": 1,
                "position": "AGREE",
                "suggestions": [
                    {
                        "id": "S1",
                        "kind": "suggestion",
                        "text": "Corrected model-owned ID.",
                    }
                ],
                "verdict": "APPROVE",
            },
        ]
    )

    generated = ReviewerAdapter(model).review_with_diagnostics(
        start_review(make_request())
    )

    assert generated.response.suggestions[0].id == "S1"
    assert generated.attempts == 2
    assert len(generated.validation_errors) == 1
    assert "string_pattern_mismatch" in generated.validation_errors[0]
    assert "api/app/tasks.py finding" not in generated.validation_errors[0]
    assert len(model.inputs) == 2
    assert (
        "previous structured response failed validation" in str(model.inputs[1]).lower()
    )


def test_reviewer_validation_retry_is_bounded_and_fails_closed() -> None:
    invalid = {
        "round": 1,
        "position": "AGREE",
        "suggestions": [
            {
                "id": "not-an-s-id",
                "kind": "suggestion",
                "text": "Malformed model-owned ID.",
            }
        ],
        "verdict": "APPROVE",
    }
    model = FakeStructuredModel([invalid, invalid])
    state = start_review(make_request())

    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        ReviewerAdapter(model).review(state)

    assert len(model.inputs) == 2
    assert state.responses == ()


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


def test_round_one_reviewer_schema_rejects_wrong_round() -> None:
    state = start_review(make_request())
    model = FakeStructuredModel(
        {
            "round": 2,
            "position": "AGREE",
            "verdict": "APPROVE",
        }
    )

    with pytest.raises(ValidationError, match="Input should be 1"):
        ReviewerAdapter(model, max_validation_attempts=1).review(state)

    assert state.responses == ()


def test_followup_reviewer_uses_full_response_schema_and_dry_run() -> None:
    blocker = Concern(id="B1", kind=ConcernKind.BLOCKING, text="Blocked.")
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
                        reason="The validation already exists.",
                    ),
                ),
                revised_diff="Unchanged — see Review Request.",
                requesting=RebuttalRequest.RE_REVIEW,
            ),
        ),
    )
    model = FakeStructuredModel(
        {
            "round": 3,
            "position": "DISAGREE",
            "verdict": "REVISE",
        }
    )

    with pytest.raises(InvalidTransition, match="expected review response round 2"):
        ReviewerAdapter(model, max_validation_attempts=1).review(state)

    assert model.schemas == [ReviewResponse]
    assert state.responses == (
        ReviewResponse(
            round=1,
            position="DISAGREE",
            blocking_concerns=(blocker,),
            verdict="REVISE",
        ),
    )


def _round_two_state_with_disputed_concern():
    """Mirror the stuck round-2 review: B1 disputed, B2/B3 accepted, R1/R2."""

    blockers = tuple(
        Concern(id=f"B{number}", kind=ConcernKind.BLOCKING, text=f"Blocker {number}.")
        for number in (1, 2, 3)
    )
    return replay_review(
        make_request(),
        (
            ReviewResponse(
                round=1,
                position="DISAGREE",
                blocking_concerns=blockers,
                verdict="REVISE",
            ),
            Rebuttal(
                round=1,
                position="PARTIAL",
                blocking_responses=(
                    BlockingConcernResponse(
                        concern_id="B1",
                        disposition=Disposition.DISPUTE,
                        reason="The guard already exists upstream.",
                    ),
                    BlockingConcernResponse(
                        concern_id="B2",
                        disposition=Disposition.ACCEPT,
                        reason="Fixed in the revised diff.",
                    ),
                    BlockingConcernResponse(
                        concern_id="B3",
                        disposition=Disposition.ACCEPT,
                        reason="Fixed in the revised diff.",
                    ),
                ),
                new_points=(
                    RebuttalPoint(id="R1", text="First worker point."),
                    RebuttalPoint(id="R2", text="Second worker point."),
                ),
                revised_diff="+revised = True",
                requesting=RebuttalRequest.RE_REVIEW,
            ),
        ),
    )


_ROUND_TWO_RELISTS_OPEN_CONCERN = {
    "round": 2,
    "position": "PARTIAL",
    "blocking_concerns": [
        {"id": "B1", "kind": "blocking", "text": "Still blocked: restated."}
    ],
    "resolved_concern_ids": ["B2", "B3"],
    "prior_point_responses": [
        {"point_id": "R1", "response": "Noted."},
        {"point_id": "R2", "response": "Noted."},
    ],
    "verdict": "REVISE",
}

_ROUND_TWO_KEEPS_CONCERN_OPEN = {
    "round": 2,
    "position": "PARTIAL",
    "resolved_concern_ids": ["B2", "B3"],
    "prior_concern_responses": [
        {"concern_id": "B1", "response": "The upstream guard misses retries."}
    ],
    "prior_point_responses": [
        {"point_id": "R1", "response": "Noted."},
        {"point_id": "R2", "response": "Noted."},
    ],
    "verdict": "REVISE",
}


def test_reviewer_retries_lifecycle_rejection_with_feedback() -> None:
    state = _round_two_state_with_disputed_concern()
    model = FakeStructuredModel(
        [_ROUND_TWO_RELISTS_OPEN_CONCERN, _ROUND_TWO_KEEPS_CONCERN_OPEN]
    )

    generated = ReviewerAdapter(model).review_with_diagnostics(state)

    assert generated.attempts == 2
    assert generated.response.blocking_concerns == ()
    assert generated.response.resolved_concern_ids == ("B2", "B3")
    assert len(generated.validation_errors) == 1
    assert "InvalidTransition" in generated.validation_errors[0]
    assert "expected new B IDs ('B4',), got ('B1',)" in generated.validation_errors[0]
    assert "expected new B IDs ('B4',), got ('B1',)" in model.inputs[1][-1].content
    assert (
        apply_review_response(state, generated.response).open_blocking_concerns[0].id
        == "B1"
    )


def test_reviewer_lifecycle_retry_is_bounded_and_reports_rejection() -> None:
    state = _round_two_state_with_disputed_concern()
    model = FakeStructuredModel(
        [_ROUND_TWO_RELISTS_OPEN_CONCERN, _ROUND_TWO_RELISTS_OPEN_CONCERN]
    )
    rejected: list[RejectedGeneration] = []

    with pytest.raises(InvalidTransition, match="expected new B IDs"):
        ReviewerAdapter(model, on_rejected=rejected.append).review(state)

    assert len(model.inputs) == 2
    assert len(rejected) == 1
    assert rejected[0].attempts == 2
    assert len(rejected[0].validation_errors) == 2
    # The rejected output is reported as generated, not repaired.
    assert isinstance(rejected[0].response, ReviewResponse)
    assert rejected[0].response.blocking_concerns[0].id == "B1"
    assert state.responses[-1].round == 1


def test_reviewer_schema_exhaustion_reports_raw_rejection() -> None:
    invalid = {"round": 1, "position": "AGREE", "verdict": "MAYBE"}
    model = FakeStructuredModel([invalid])
    rejected: list[RejectedGeneration] = []

    with pytest.raises(ValidationError):
        ReviewerAdapter(
            model, max_validation_attempts=1, on_rejected=rejected.append
        ).review(start_review(make_request()))

    assert rejected == [
        RejectedGeneration(
            response=invalid,
            attempts=1,
            validation_errors=rejected[0].validation_errors,
        )
    ]
    assert "verdict" in rejected[0].validation_errors[0]


def test_reviewer_must_answer_dispute_it_keeps_open() -> None:
    unanswered = {
        key: value
        for key, value in _ROUND_TWO_KEEPS_CONCERN_OPEN.items()
        if key != "prior_concern_responses"
    }
    model = FakeStructuredModel([unanswered, _ROUND_TWO_KEEPS_CONCERN_OPEN])

    generated = ReviewerAdapter(model).review_with_diagnostics(
        _round_two_state_with_disputed_concern()
    )

    assert generated.attempts == 2
    assert "missing ('B1',)" in generated.validation_errors[0]
    assert generated.response.prior_concern_responses[0].concern_id == "B1"


def test_reviewer_need_not_answer_dispute_it_resolves() -> None:
    resolves_all = {
        **_ROUND_TWO_KEEPS_CONCERN_OPEN,
        "resolved_concern_ids": ["B1", "B2", "B3"],
        "prior_concern_responses": [],
        "verdict": "APPROVE",
    }
    model = FakeStructuredModel([resolves_all])

    generated = ReviewerAdapter(model).review_with_diagnostics(
        _round_two_state_with_disputed_concern()
    )

    assert generated.attempts == 1


def test_reviewer_prompt_states_lifecycle_field_rules() -> None:
    model = FakeStructuredModel([_ROUND_TWO_KEEPS_CONCERN_OPEN])

    ReviewerAdapter(model).review(_round_two_state_with_disputed_concern())

    system_message = model.inputs[0][0].content
    assert "B4" in system_message
    assert "leave its ID out of both resolved_concern_ids" in system_message
    assert "prior_point_responses must answer every R point" in system_message
    assert "APPROVE requires that no blocking concern stays open" in system_message
    assert "prior_concern_responses" in system_message


def test_worker_prompt_omits_reviewer_field_rules() -> None:
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
                    "reason": "Already handled.",
                }
            ],
            "revised_diff": "Unchanged — see Review Request.",
            "requesting": "RE-REVIEW",
        }
    )

    WorkerAdapter(model, rebuttal_schema=Rebuttal).respond(state)

    assert "resolved_concern_ids" not in model.inputs[0][0].content


def test_reviewer_revalidates_malformed_model_output() -> None:
    model = FakeStructuredModel({"round": 1})

    with pytest.raises(ValidationError):
        ReviewerAdapter(model, max_validation_attempts=1).review(
            start_review(make_request())
        )


def test_reviewer_revalidates_preconstructed_model_instance() -> None:
    invalid = ReviewResponse.model_construct(
        round=4,
        position="AGREE",
        verdict="APPROVE",
    )
    model = FakeStructuredModel(invalid)

    with pytest.raises(ValidationError):
        ReviewerAdapter(model, max_validation_attempts=1).review(
            start_review(make_request())
        )


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

    rebuttal = WorkerAdapter(model, rebuttal_schema=Rebuttal).respond(state)

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
        WorkerAdapter(model, rebuttal_schema=Rebuttal).respond(state)


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
        WorkerAdapter(model, rebuttal_schema=Rebuttal).respond(state)


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

    rebuttal = WorkerAdapter(model, rebuttal_schema=Rebuttal).respond(
        state, revised_diff=revised_diff
    )

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
        WorkerAdapter(model, rebuttal_schema=Rebuttal).respond(
            state, revised_diff="+authoritative = True"
        )


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

    summary = WorkerAdapter(model, rebuttal_schema=Rebuttal).respond(state)

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
        WorkerAdapter(model, rebuttal_schema=Rebuttal).respond(
            state, revised_diff="+irrelevant = True"
        )

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

    final_position = WorkerAdapter(model, rebuttal_schema=Rebuttal).respond(state)

    assert isinstance(final_position, Rebuttal)
    assert model.schemas == [Rebuttal]


def test_worker_rejects_wrong_status_before_model_call() -> None:
    model = FakeStructuredModel({})

    with pytest.raises(InvalidTransition, match="worker cannot act"):
        WorkerAdapter(model, rebuttal_schema=Rebuttal).respond(
            start_review(make_request())
        )

    assert model.inputs == []
