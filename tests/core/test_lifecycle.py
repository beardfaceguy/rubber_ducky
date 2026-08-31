import pytest

from rubber_ducky.core.lifecycle import (
    InvalidTransition,
    ReviewState,
    ReviewStatus,
    apply_event,
    apply_rebuttal,
    apply_review_response,
    finalize_escalation,
    replay_review,
    start_review,
)
from rubber_ducky.code.models import Rebuttal, ReviewRequest
from rubber_ducky.core.models import (
    BlockingConcernResponse,
    Concern,
    ConcernKind,
    Disposition,
    EscalationConcern,
    EscalationSummary,
    Position,
    PriorPointResponse,
    RebuttalPoint,
    RebuttalRequest,
    ReviewResponse,
    Verdict,
)


def make_request() -> ReviewRequest:
    return ReviewRequest(
        task_id="AR-2",
        title="Implement deterministic lifecycle",
        proposed_solution="Use immutable state transitions.",
        relevant_diff="+def start_review(...): ...",
    )


def await_second_response(
    new_points: tuple[RebuttalPoint, ...] = (),
) -> ReviewState:
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position=Position.PARTIAL,
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="First blocker."),
            ),
            verdict=Verdict.REVISE,
        ),
    )
    return apply_rebuttal(
        state,
        Rebuttal(
            round=1,
            position=Position.DISAGREE,
            blocking_responses=(
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.DISPUTE,
                    reason="The first concern is disputed.",
                ),
            ),
            revised_diff="Unchanged — see Review Request.",
            new_points=new_points,
            requesting=RebuttalRequest.RE_REVIEW,
        ),
    )


def test_start_review_awaits_first_response() -> None:
    state = start_review(make_request())

    assert state.status is ReviewStatus.AWAITING_REVIEW_RESPONSE
    assert state.request == make_request()
    assert state.responses == ()
    assert state.rebuttals == ()


def test_replay_reconstructs_canonical_state() -> None:
    request = make_request()
    response = ReviewResponse(
        round=1,
        position=Position.AGREE,
        verdict=Verdict.APPROVE,
    )
    expected = apply_review_response(start_review(request), response)

    replayed = replay_review(request, (response,))

    assert replayed == expected


def test_apply_event_dispatches_to_domain_transition() -> None:
    state = start_review(make_request())
    response = ReviewResponse(
        round=1,
        position=Position.AGREE,
        verdict=Verdict.APPROVE,
    )

    assert apply_event(state, response) == apply_review_response(state, response)


def test_replay_rejects_invalid_order_with_event_context() -> None:
    rebuttal = Rebuttal(
        round=1,
        position=Position.DISAGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.DISPUTE,
                reason="Out-of-order rebuttal.",
            ),
        ),
        revised_diff="Unchanged — see Review Request.",
        requesting=RebuttalRequest.RE_REVIEW,
    )

    with pytest.raises(
        InvalidTransition,
        match=r"invalid event 1 \(Rebuttal\)",
    ):
        replay_review(make_request(), (rebuttal,))


def test_replay_dispatches_escalation_summary() -> None:
    response = ReviewResponse(
        round=1,
        position=Position.DISAGREE,
        blocking_concerns=(
            Concern(id="B1", kind=ConcernKind.BLOCKING, text="Deadlocked."),
        ),
        verdict=Verdict.ESCALATE,
    )
    summary = EscalationSummary(
        unresolved_blocking_concerns=(
            EscalationConcern(concern_id="B1", status="Still disputed."),
        ),
        worker_position="The implementation is safe.",
        reviewer_position="The blocker remains.",
        decision_needed="Should implementation continue?",
    )

    replayed = replay_review(make_request(), (response, summary))

    assert replayed.status is ReviewStatus.ESCALATED
    assert replayed.escalation_summary == summary


def test_replay_dispatches_complete_multi_round_history() -> None:
    request = make_request()
    blocker = Concern(id="B1", kind=ConcernKind.BLOCKING, text="First blocker.")
    first_response = ReviewResponse(
        round=1,
        position=Position.PARTIAL,
        blocking_concerns=(blocker,),
        verdict=Verdict.REVISE,
    )
    rebuttal = Rebuttal(
        round=1,
        position=Position.DISAGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.DISPUTE,
                reason="The blocker is disputed.",
            ),
        ),
        revised_diff="Unchanged — see Review Request.",
        requesting=RebuttalRequest.RE_REVIEW,
    )
    approval = ReviewResponse(
        round=2,
        position=Position.AGREE,
        resolved_concern_ids=("B1",),
        verdict=Verdict.APPROVE,
    )
    expected = apply_review_response(
        apply_rebuttal(
            apply_review_response(start_review(request), first_response),
            rebuttal,
        ),
        approval,
    )

    replayed = replay_review(request, (first_response, rebuttal, approval))

    assert replayed == expected


def test_replay_rejects_unsupported_runtime_event() -> None:
    with pytest.raises(
        InvalidTransition,
        match=r"invalid event 1 \(str\): unsupported review event type: str",
    ):
        replay_review(make_request(), ("not a protocol event",))  # type: ignore[arg-type]


def test_approve_response_completes_review() -> None:
    state = start_review(make_request())
    response = ReviewResponse(
        round=1,
        position=Position.AGREE,
        verdict=Verdict.APPROVE,
    )

    completed = apply_review_response(state, response)

    assert completed.status is ReviewStatus.APPROVED
    assert completed.responses == (response,)


def test_response_round_must_match_conversation() -> None:
    state = start_review(make_request())
    wrong_round = ReviewResponse(
        round=2,
        position=Position.AGREE,
        verdict=Verdict.APPROVE,
    )

    with pytest.raises(InvalidTransition):
        apply_review_response(state, wrong_round)


def test_response_rejects_state_missing_prior_rebuttal() -> None:
    blocker = Concern(id="B1", kind=ConcernKind.BLOCKING, text="First blocker.")
    prior_response = ReviewResponse(
        round=1,
        position=Position.PARTIAL,
        blocking_concerns=(blocker,),
        verdict=Verdict.REVISE,
    )
    incomplete_state = ReviewState(
        status=ReviewStatus.AWAITING_REVIEW_RESPONSE,
        request=make_request(),
        responses=(prior_response,),
        open_blocking_concerns=(blocker,),
    )
    second_response = ReviewResponse(
        round=2,
        position=Position.DISAGREE,
        verdict=Verdict.REVISE,
    )

    with pytest.raises(InvalidTransition):
        apply_review_response(incomplete_state, second_response)


def test_revise_response_opens_blocker_and_awaits_rebuttal() -> None:
    state = start_review(make_request())
    blocker = Concern(
        id="B1",
        kind=ConcernKind.BLOCKING,
        text="The reducer skips concern validation.",
    )
    response = ReviewResponse(
        round=1,
        position=Position.PARTIAL,
        blocking_concerns=(blocker,),
        verdict=Verdict.REVISE,
    )

    revised = apply_review_response(state, response)

    assert revised.status is ReviewStatus.AWAITING_REBUTTAL
    assert revised.open_blocking_concerns == (blocker,)


def test_rebuttal_returns_review_to_reviewer() -> None:
    blocker = Concern(
        id="B1",
        kind=ConcernKind.BLOCKING,
        text="The reducer skips concern validation.",
    )
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position=Position.PARTIAL,
            blocking_concerns=(blocker,),
            verdict=Verdict.REVISE,
        ),
    )
    rebuttal = Rebuttal(
        round=1,
        position=Position.DISAGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.DISPUTE,
                reason="The validation occurs before this transition.",
            ),
        ),
        revised_diff="Unchanged — see Review Request.",
        requesting=RebuttalRequest.RE_REVIEW,
    )

    awaiting_review = apply_rebuttal(state, rebuttal)

    assert awaiting_review.status is ReviewStatus.AWAITING_REVIEW_RESPONSE
    assert awaiting_review.rebuttals == (rebuttal,)


def test_rebuttal_must_address_every_open_blocker() -> None:
    blockers = (
        Concern(id="B1", kind=ConcernKind.BLOCKING, text="First blocker."),
        Concern(id="B2", kind=ConcernKind.BLOCKING, text="Second blocker."),
    )
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position=Position.PARTIAL,
            blocking_concerns=blockers,
            verdict=Verdict.REVISE,
        ),
    )
    incomplete = Rebuttal(
        round=1,
        position=Position.PARTIAL,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.ACCEPT,
                reason="The first concern is valid.",
            ),
        ),
        revised_diff="+first_fix = True",
        requesting=RebuttalRequest.RE_REVIEW,
    )

    with pytest.raises(InvalidTransition):
        apply_rebuttal(state, incomplete)


def test_new_concern_ids_continue_conversation_sequence() -> None:
    first_blocker = Concern(
        id="B1",
        kind=ConcernKind.BLOCKING,
        text="First blocker.",
    )
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position=Position.PARTIAL,
            blocking_concerns=(first_blocker,),
            verdict=Verdict.REVISE,
        ),
    )
    state = apply_rebuttal(
        state,
        Rebuttal(
            round=1,
            position=Position.DISAGREE,
            blocking_responses=(
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.DISPUTE,
                    reason="The first concern is disputed.",
                ),
            ),
            revised_diff="Unchanged — see Review Request.",
            requesting=RebuttalRequest.RE_REVIEW,
        ),
    )
    skipped_id = ReviewResponse(
        round=2,
        position=Position.PARTIAL,
        blocking_concerns=(
            Concern(id="B3", kind=ConcernKind.BLOCKING, text="Skipped B2."),
        ),
        verdict=Verdict.REVISE,
    )

    with pytest.raises(InvalidTransition):
        apply_review_response(state, skipped_id)


def test_suggestion_ids_start_at_one() -> None:
    state = start_review(make_request())
    skipped_id = ReviewResponse(
        round=1,
        position=Position.AGREE,
        suggestions=(
            Concern(id="S2", kind=ConcernKind.SUGGESTION, text="Skipped S1."),
        ),
        verdict=Verdict.APPROVE,
    )

    with pytest.raises(InvalidTransition):
        apply_review_response(state, skipped_id)


def test_response_can_resolve_only_open_concerns() -> None:
    state = await_second_response()
    unknown_resolution = ReviewResponse(
        round=2,
        position=Position.PARTIAL,
        resolved_concern_ids=("B2",),
        verdict=Verdict.REVISE,
    )

    with pytest.raises(InvalidTransition):
        apply_review_response(state, unknown_resolution)


def test_resolving_last_blocker_allows_approval() -> None:
    state = await_second_response()
    approval = ReviewResponse(
        round=2,
        position=Position.AGREE,
        resolved_concern_ids=("B1",),
        verdict=Verdict.APPROVE,
    )

    completed = apply_review_response(state, approval)

    assert completed.status is ReviewStatus.APPROVED
    assert completed.open_blocking_concerns == ()


def test_response_must_address_every_prior_rebuttal_point() -> None:
    state = await_second_response(
        new_points=(RebuttalPoint(id="R1", text="New evidence."),)
    )
    missing_response = ReviewResponse(
        round=2,
        position=Position.AGREE,
        resolved_concern_ids=("B1",),
        verdict=Verdict.APPROVE,
    )

    with pytest.raises(InvalidTransition):
        apply_review_response(state, missing_response)


def test_rebuttal_point_ids_continue_conversation_sequence() -> None:
    state = await_second_response(
        new_points=(RebuttalPoint(id="R1", text="First point."),)
    )
    state = apply_review_response(
        state,
        ReviewResponse(
            round=2,
            position=Position.PARTIAL,
            blocking_concerns=(
                Concern(id="B2", kind=ConcernKind.BLOCKING, text="Second blocker."),
            ),
            resolved_concern_ids=("B1",),
            prior_point_responses=(
                PriorPointResponse(point_id="R1", response="Addressed."),
            ),
            verdict=Verdict.REVISE,
        ),
    )
    skipped_id = Rebuttal(
        round=2,
        position=Position.DISAGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B2",
                disposition=Disposition.DISPUTE,
                reason="The blocker is disputed.",
            ),
        ),
        revised_diff="Unchanged — see Review Request.",
        new_points=(RebuttalPoint(id="R3", text="Skipped R2."),),
        requesting=RebuttalRequest.RE_REVIEW,
    )

    with pytest.raises(InvalidTransition):
        apply_rebuttal(state, skipped_id)


def test_worker_can_withdraw_after_revise() -> None:
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position=Position.PARTIAL,
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="First blocker."),
            ),
            verdict=Verdict.REVISE,
        ),
    )
    withdrawal = Rebuttal(
        round=1,
        position=Position.AGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.ACCEPT,
                reason="The concern is valid, but work is stopping.",
            ),
        ),
        revised_diff="+work_stopped = True",
        requesting=RebuttalRequest.WITHDRAWN,
    )

    withdrawn = apply_rebuttal(state, withdrawal)

    assert withdrawn.status is ReviewStatus.WITHDRAWN


def test_round_three_revise_accepts_final_position_then_requires_summary() -> None:
    state = await_second_response()
    state = apply_review_response(
        state,
        ReviewResponse(
            round=2,
            position=Position.DISAGREE,
            verdict=Verdict.REVISE,
        ),
    )
    state = apply_rebuttal(
        state,
        Rebuttal(
            round=2,
            position=Position.DISAGREE,
            blocking_responses=(
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.DISPUTE,
                    reason="The blocker remains disputed.",
                ),
            ),
            revised_diff="Unchanged — see Review Request.",
            requesting=RebuttalRequest.RE_REVIEW,
        ),
    )

    state = apply_review_response(
        state,
        ReviewResponse(
            round=3,
            position=Position.DISAGREE,
            verdict=Verdict.REVISE,
        ),
    )

    assert state.status is ReviewStatus.AWAITING_FINAL_POSITION

    direct_summary = EscalationSummary(
        unresolved_blocking_concerns=(
            EscalationConcern(concern_id="B1", status="Still disputed."),
        ),
        worker_position="Final worker position.",
        reviewer_position="The blocker remains.",
        decision_needed="Should implementation continue?",
    )
    directly_escalated = finalize_escalation(state, direct_summary)
    assert directly_escalated.status is ReviewStatus.ESCALATED

    state = apply_rebuttal(
        state,
        Rebuttal(
            round=3,
            position=Position.DISAGREE,
            blocking_responses=(
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.DISPUTE,
                    reason="Final worker position.",
                ),
            ),
            revised_diff="Unchanged — see Review Request.",
            requesting=RebuttalRequest.FINAL_POSITION,
        ),
    )

    assert state.status is ReviewStatus.AWAITING_ESCALATION_SUMMARY


def test_round_three_escalate_allows_optional_final_position() -> None:
    state = await_second_response()
    state = apply_review_response(
        state,
        ReviewResponse(
            round=2,
            position=Position.DISAGREE,
            verdict=Verdict.REVISE,
        ),
    )
    state = apply_rebuttal(
        state,
        Rebuttal(
            round=2,
            position=Position.DISAGREE,
            blocking_responses=(
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.DISPUTE,
                    reason="The blocker remains disputed.",
                ),
            ),
            revised_diff="Unchanged — see Review Request.",
            requesting=RebuttalRequest.RE_REVIEW,
        ),
    )

    state = apply_review_response(
        state,
        ReviewResponse(
            round=3,
            position=Position.DISAGREE,
            verdict=Verdict.ESCALATE,
        ),
    )

    assert state.status is ReviewStatus.AWAITING_FINAL_POSITION

    withdrawn = apply_rebuttal(
        state,
        Rebuttal(
            round=3,
            position=Position.DISAGREE,
            blocking_responses=(
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.DISPUTE,
                    reason="The worker withdraws instead of escalating.",
                ),
            ),
            revised_diff="Unchanged — see Review Request.",
            requesting=RebuttalRequest.WITHDRAWN,
        ),
    )
    assert withdrawn.status is ReviewStatus.WITHDRAWN


def test_reviewer_escalation_is_finalized_with_matching_summary() -> None:
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position=Position.DISAGREE,
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="Deadlocked."),
            ),
            verdict=Verdict.ESCALATE,
        ),
    )
    summary = EscalationSummary(
        unresolved_blocking_concerns=(
            EscalationConcern(concern_id="B1", status="Still disputed."),
        ),
        worker_position="The implementation is safe.",
        reviewer_position="The blocker remains.",
        decision_needed="Should implementation continue?",
    )

    escalated = finalize_escalation(state, summary)

    assert escalated.status is ReviewStatus.ESCALATED
    assert escalated.escalation_summary == summary


def test_escalation_summary_must_cover_exact_open_blockers() -> None:
    state = apply_review_response(
        start_review(make_request()),
        ReviewResponse(
            round=1,
            position=Position.DISAGREE,
            blocking_concerns=(
                Concern(id="B1", kind=ConcernKind.BLOCKING, text="First blocker."),
                Concern(id="B2", kind=ConcernKind.BLOCKING, text="Second blocker."),
            ),
            verdict=Verdict.ESCALATE,
        ),
    )
    incomplete = EscalationSummary(
        unresolved_blocking_concerns=(
            EscalationConcern(concern_id="B1", status="Still disputed."),
        ),
        worker_position="The implementation is safe.",
        reviewer_position="Two blockers remain.",
        decision_needed="Should implementation continue?",
    )

    with pytest.raises(InvalidTransition):
        finalize_escalation(state, incomplete)
