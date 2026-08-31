from langgraph.types import Command

from rubber_ducky.code.models import (
    CODE_CHECKPOINT_TYPES,
    CodeReviewState,
    Rebuttal,
    ReviewRequest,
)
from rubber_ducky.core.lifecycle import ReviewStatus
from rubber_ducky.core.models import (
    BlockingConcernResponse,
    Concern,
    ConcernKind,
    Disposition,
    EscalationConcern,
    EscalationSummary,
    Position,
    RebuttalRequest,
    ReviewResponse,
    Verdict,
)
from rubber_ducky.core.workflow import build_review_graph as _build_review_graph


def build_review_graph():
    return _build_review_graph(
        state_cls=CodeReviewState,
        additional_types=CODE_CHECKPOINT_TYPES,
    )


def make_request() -> ReviewRequest:
    return ReviewRequest(
        task_id="AR-4",
        title="Wrap reducer in LangGraph",
        proposed_solution="Pause for typed participant events.",
        relevant_diff="+graph = StateGraph(...)",
    )


def test_workflow_starts_and_interrupts_for_review_response() -> None:
    graph = build_review_graph()
    config = {"configurable": {"thread_id": "review-1"}}

    paused = graph.invoke({"request": make_request()}, config)

    assert paused["review"].status is ReviewStatus.AWAITING_REVIEW_RESPONSE
    assert paused["__interrupt__"][0].value == {
        "status": ReviewStatus.AWAITING_REVIEW_RESPONSE.value,
        "round": 1,
        "event_type": "review_response",
    }


def test_fake_reviewer_can_resume_to_approval() -> None:
    graph = build_review_graph()
    config = {"configurable": {"thread_id": "review-approval"}}
    graph.invoke({"request": make_request()}, config)
    approval = ReviewResponse(
        round=1,
        position=Position.AGREE,
        verdict=Verdict.APPROVE,
    )

    completed = graph.invoke(Command(resume=approval), config)

    assert completed["review"].status is ReviewStatus.APPROVED
    assert "event" not in completed
    assert not completed.get("__interrupt__")
    assert graph.get_state(config).values["review"] == completed["review"]


def test_fake_participants_resume_multi_round_review() -> None:
    graph = build_review_graph()
    config = {"configurable": {"thread_id": "review-multi-round"}}
    graph.invoke({"request": make_request()}, config)
    blocker = Concern(
        id="B1",
        kind=ConcernKind.BLOCKING,
        text="The graph skipped validation.",
    )
    revision = ReviewResponse(
        round=1,
        position=Position.PARTIAL,
        blocking_concerns=(blocker,),
        verdict=Verdict.REVISE,
    )

    awaiting_worker = graph.invoke(Command(resume=revision), config)

    assert awaiting_worker["__interrupt__"][0].value == {
        "status": ReviewStatus.AWAITING_REBUTTAL.value,
        "round": 1,
        "event_type": "rebuttal",
    }

    rebuttal = Rebuttal(
        round=1,
        position=Position.DISAGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.DISPUTE,
                reason="The reducer validates every event.",
            ),
        ),
        revised_diff="Unchanged — see Review Request.",
        requesting=RebuttalRequest.RE_REVIEW,
    )
    awaiting_reviewer = graph.invoke(Command(resume=rebuttal), config)

    assert awaiting_reviewer["__interrupt__"][0].value == {
        "status": ReviewStatus.AWAITING_REVIEW_RESPONSE.value,
        "round": 2,
        "event_type": "review_response",
    }

    approval = ReviewResponse(
        round=2,
        position=Position.AGREE,
        resolved_concern_ids=("B1",),
        verdict=Verdict.APPROVE,
    )
    completed = graph.invoke(Command(resume=approval), config)

    assert completed["review"].status is ReviewStatus.APPROVED
    assert completed["review"].responses == (revision, approval)
    assert completed["review"].rebuttals == (rebuttal,)


def test_fake_worker_can_resume_escalation_summary() -> None:
    graph = build_review_graph()
    config = {"configurable": {"thread_id": "review-escalation"}}
    graph.invoke({"request": make_request()}, config)
    escalation = ReviewResponse(
        round=1,
        position=Position.DISAGREE,
        blocking_concerns=(
            Concern(id="B1", kind=ConcernKind.BLOCKING, text="Deadlocked."),
        ),
        verdict=Verdict.ESCALATE,
    )

    awaiting_summary = graph.invoke(Command(resume=escalation), config)

    assert awaiting_summary["__interrupt__"][0].value == {
        "status": ReviewStatus.AWAITING_ESCALATION_SUMMARY.value,
        "event_type": "escalation_summary",
    }

    summary = EscalationSummary(
        unresolved_blocking_concerns=(
            EscalationConcern(concern_id="B1", status="Still disputed."),
        ),
        worker_position="The implementation is safe.",
        reviewer_position="The blocker remains.",
        decision_needed="Should implementation continue?",
    )
    completed = graph.invoke(Command(resume=summary), config)

    assert completed["review"].status is ReviewStatus.ESCALATED
    assert completed["review"].escalation_summary == summary


def test_round_three_final_position_interrupts_before_escalation() -> None:
    graph = build_review_graph()
    config = {"configurable": {"thread_id": "review-final-position"}}
    graph.invoke({"request": make_request()}, config)
    blocker = Concern(id="B1", kind=ConcernKind.BLOCKING, text="Still blocked.")
    events = (
        ReviewResponse(
            round=1,
            position=Position.DISAGREE,
            blocking_concerns=(blocker,),
            verdict=Verdict.REVISE,
        ),
        Rebuttal(
            round=1,
            position=Position.DISAGREE,
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
            position=Position.DISAGREE,
            verdict=Verdict.REVISE,
        ),
        Rebuttal(
            round=2,
            position=Position.DISAGREE,
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
            position=Position.DISAGREE,
            verdict=Verdict.REVISE,
        ),
    )

    for event in events:
        paused = graph.invoke(Command(resume=event), config)

    assert paused["__interrupt__"][0].value == {
        "status": ReviewStatus.AWAITING_FINAL_POSITION.value,
        "round": 3,
        "event_type": "rebuttal",
    }

    final_position = Rebuttal(
        round=3,
        position=Position.DISAGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.DISPUTE,
                reason="Final position.",
            ),
        ),
        revised_diff="Unchanged — see Review Request.",
        requesting=RebuttalRequest.FINAL_POSITION,
    )
    awaiting_summary = graph.invoke(Command(resume=final_position), config)

    assert awaiting_summary["__interrupt__"][0].value == {
        "status": ReviewStatus.AWAITING_ESCALATION_SUMMARY.value,
        "event_type": "escalation_summary",
    }


def test_invalid_resume_reprompts_with_reducer_error() -> None:
    graph = build_review_graph()
    config = {"configurable": {"thread_id": "review-invalid"}}
    graph.invoke({"request": make_request()}, config)
    out_of_order = Rebuttal(
        round=1,
        position=Position.DISAGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.DISPUTE,
                reason="No review response preceded this rebuttal.",
            ),
        ),
        revised_diff="Unchanged — see Review Request.",
        requesting=RebuttalRequest.RE_REVIEW,
    )

    reprompted = graph.invoke(Command(resume=out_of_order), config)

    assert reprompted["__interrupt__"][0].value == {
        "status": ReviewStatus.AWAITING_REVIEW_RESPONSE.value,
        "round": 1,
        "event_type": "review_response",
        "error": "cannot rebut while status is awaiting_review_response",
    }

    completed = graph.invoke(
        Command(
            resume=ReviewResponse(
                round=1,
                position=Position.AGREE,
                verdict=Verdict.APPROVE,
            )
        ),
        config,
    )
    assert completed["review"].status is ReviewStatus.APPROVED


def test_in_memory_checkpointer_isolates_review_threads() -> None:
    graph = build_review_graph()
    first_config = {"configurable": {"thread_id": "review-first"}}
    second_config = {"configurable": {"thread_id": "review-second"}}
    graph.invoke({"request": make_request()}, first_config)
    second_request = make_request().model_copy(update={"task_id": "AR-5"})
    graph.invoke({"request": second_request}, second_config)

    graph.invoke(
        Command(
            resume=ReviewResponse(
                round=1,
                position=Position.AGREE,
                verdict=Verdict.APPROVE,
            )
        ),
        first_config,
    )

    assert (
        graph.get_state(first_config).values["review"].status is ReviewStatus.APPROVED
    )
    second_state = graph.get_state(second_config)
    assert second_state.values["review"].status is ReviewStatus.AWAITING_REVIEW_RESPONSE
    assert second_state.values["request"] == second_request
