import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from langgraph.types import Command

from rubber_ducky.code.models import (
    CODE_CHECKPOINT_TYPES,
    CodeReviewState,
    Rebuttal,
    ReviewRequest,
)
from rubber_ducky.core.lifecycle import InvalidTransition, ReviewStatus
from rubber_ducky.core.models import (
    BlockingConcernResponse,
    Concern,
    ConcernKind,
    Disposition,
    EscalationConcern,
    EscalationSummary,
    RebuttalRequest,
    ReviewResponse,
)
from rubber_ducky.core.persistence import (
    PersistenceConflict,
    SqliteReviewStore,
    sqlite_review_checkpointer,
)
from rubber_ducky.core.workflow import build_review_graph as _build_review_graph


def _store(database: object) -> SqliteReviewStore:
    return SqliteReviewStore(
        database,
        request_model=ReviewRequest,
        rebuttal_model=Rebuttal,
        state_model=CodeReviewState,
    )


def _checkpointer(database: object):
    return sqlite_review_checkpointer(database, CODE_CHECKPOINT_TYPES)


def _graph(checkpointer):
    return _build_review_graph(
        checkpointer,
        state_cls=CodeReviewState,
        additional_types=CODE_CHECKPOINT_TYPES,
    )


def make_request() -> ReviewRequest:
    return ReviewRequest(
        task_id="AR-6",
        title="Add durable persistence",
        proposed_solution="Persist events and replay canonical state.",
        relevant_diff="+store = SqliteReviewStore(path)",
    )


def create_review(
    store: SqliteReviewStore,
    request: ReviewRequest | None = None,
):
    return store.create_review(
        "review-1",
        make_request() if request is None else request,
        audit_slug="persistence",
    )


def test_review_survives_store_reopen(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite"
    first_store = _store(database)
    created = create_review(first_store)

    reopened = _store(database).load_review("review-1")

    assert created.status is ReviewStatus.AWAITING_REVIEW_RESPONSE
    assert reopened == created


def test_create_review_is_idempotent_for_same_request(tmp_path: Path) -> None:
    store = _store(tmp_path / "reviews.sqlite")
    first = create_review(store)

    duplicate = create_review(store)

    assert duplicate == first


def test_duplicate_create_returns_current_replayed_state(tmp_path: Path) -> None:
    store = _store(tmp_path / "reviews.sqlite")
    create_review(store)
    store.append_event(
        "review-1",
        "event-1",
        ReviewResponse(round=1, position="AGREE", verdict="APPROVE"),
    )

    duplicate = create_review(store)

    assert duplicate.status is ReviewStatus.APPROVED


def test_create_review_rejects_conflicting_request(tmp_path: Path) -> None:
    store = _store(tmp_path / "reviews.sqlite")
    create_review(store)
    conflicting = make_request().model_copy(
        update={"relevant_diff": "+different = True"}
    )

    with pytest.raises(PersistenceConflict):
        create_review(store, conflicting)


def test_persisted_event_replays_after_reopen(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite"
    store = _store(database)
    create_review(store)
    approval = ReviewResponse(
        round=1,
        position="AGREE",
        verdict="APPROVE",
    )

    completed = store.append_event("review-1", "event-1", approval)
    reopened = _store(database).load_review("review-1")

    assert completed.status is ReviewStatus.APPROVED
    assert reopened == completed


def test_append_event_is_idempotent_for_same_event(tmp_path: Path) -> None:
    store = _store(tmp_path / "reviews.sqlite")
    create_review(store)
    approval = ReviewResponse(
        round=1,
        position="AGREE",
        verdict="APPROVE",
    )
    first = store.append_event("review-1", "event-1", approval)

    duplicate = store.append_event("review-1", "event-1", approval)

    assert duplicate == first


def test_append_event_rejects_conflicting_idempotency_key(tmp_path: Path) -> None:
    store = _store(tmp_path / "reviews.sqlite")
    create_review(store)
    store.append_event(
        "review-1",
        "event-1",
        ReviewResponse(round=1, position="AGREE", verdict="APPROVE"),
    )
    conflicting = ReviewResponse(
        round=1,
        position="DISAGREE",
        verdict="APPROVE",
    )

    with pytest.raises(PersistenceConflict, match="reused"):
        store.append_event("review-1", "event-1", conflicting)


def test_event_metadata_is_secret_safe_and_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path / "reviews.sqlite")
    create_review(store)
    approval = ReviewResponse(round=1, position="AGREE", verdict="APPROVE")
    metadata = {"provider": "openai", "model": "gpt-configured"}

    store.append_event("review-1", "event-1", approval, metadata=metadata)
    duplicate = store.append_event(
        "review-1",
        "event-1",
        approval,
        metadata=metadata,
    )

    assert duplicate.status is ReviewStatus.APPROVED
    assert store.load_history("review-1").events[0].metadata == metadata
    with pytest.raises(ValueError, match="unsupported event metadata"):
        store.append_event(
            "review-1",
            "event-2",
            approval,
            metadata={"api_key": "must-not-persist"},
        )


def test_invalid_event_rolls_back_idempotency_key(tmp_path: Path) -> None:
    store = _store(tmp_path / "reviews.sqlite")
    create_review(store)
    invalid = ReviewResponse(
        round=2,
        position="AGREE",
        verdict="APPROVE",
    )

    with pytest.raises(InvalidTransition):
        store.append_event("review-1", "event-1", invalid)

    completed = store.append_event(
        "review-1",
        "event-1",
        ReviewResponse(round=1, position="AGREE", verdict="APPROVE"),
    )
    assert completed.status is ReviewStatus.APPROVED


def test_langgraph_interrupt_resumes_after_process_reopen(tmp_path: Path) -> None:
    database = tmp_path / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": "review-1"}}
    with _checkpointer(database) as checkpointer:
        graph = _graph(checkpointer)
        paused = graph.invoke({"request": make_request()}, config)
        assert paused["__interrupt__"]

    with _checkpointer(database) as checkpointer:
        reopened_graph = _graph(checkpointer)
        completed = reopened_graph.invoke(
            Command(
                resume=ReviewResponse(
                    round=1,
                    position="AGREE",
                    verdict="APPROVE",
                )
            ),
            config,
        )

    assert completed["review"].status is ReviewStatus.APPROVED


def test_unknown_persisted_event_type_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite"
    store = _store(database)
    create_review(store)
    store.append_event(
        "review-1",
        "event-1",
        ReviewResponse(round=1, position="AGREE", verdict="APPROVE"),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE review_events SET event_type = 'unknown' WHERE thread_id = ?",
            ("review-1",),
        )

    with pytest.raises(ValueError, match="unknown persisted event type"):
        store.load_review("review-1")


def test_multi_round_history_replays_every_event_type(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite"
    store = _store(database)
    create_review(store)
    blocker = Concern(id="B1", kind=ConcernKind.BLOCKING, text="Still blocked.")
    store.append_event(
        "review-1",
        "event-1",
        ReviewResponse(
            round=1,
            position="PARTIAL",
            blocking_concerns=(blocker,),
            verdict="REVISE",
        ),
    )
    store.append_event(
        "review-1",
        "event-2",
        Rebuttal(
            round=1,
            position="DISAGREE",
            blocking_responses=(
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.DISPUTE,
                    reason="The blocker is disputed.",
                ),
            ),
            revised_diff="Unchanged — see Review Request.",
            requesting=RebuttalRequest.RE_REVIEW,
        ),
    )
    store.append_event(
        "review-1",
        "event-3",
        ReviewResponse(
            round=2,
            position="DISAGREE",
            verdict="ESCALATE",
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
    store.append_event("review-1", "event-4", summary)

    reopened = _store(database).load_review("review-1")

    assert reopened.status is ReviewStatus.ESCALATED
    assert reopened.escalation_summary == summary


def test_concurrent_duplicate_event_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path / "reviews.sqlite")
    create_review(store)
    approval = ReviewResponse(round=1, position="AGREE", verdict="APPROVE")
    barrier = Barrier(2)

    def append_duplicate() -> ReviewStatus:
        barrier.wait()
        return store.append_event("review-1", "event-1", approval).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = tuple(executor.map(lambda _: append_duplicate(), range(2)))

    assert statuses == (ReviewStatus.APPROVED, ReviewStatus.APPROVED)


def test_concurrent_distinct_events_cannot_advance_same_round_twice(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "reviews.sqlite")
    create_review(store)
    approval = ReviewResponse(round=1, position="AGREE", verdict="APPROVE")
    barrier = Barrier(2)

    def append_once(event_id: str) -> ReviewStatus:
        barrier.wait()
        return store.append_event("review-1", event_id, approval).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(append_once, event_id)
            for event_id in ("event-1", "event-2")
        ]
        outcomes: list[ReviewStatus | type[Exception]] = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except InvalidTransition as error:
                outcomes.append(type(error))

    assert outcomes.count(ReviewStatus.APPROVED) == 1
    assert outcomes.count(InvalidTransition) == 1
