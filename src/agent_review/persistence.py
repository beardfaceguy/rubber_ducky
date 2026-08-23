"""Durable SQLite event storage and LangGraph checkpointer factories."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from agent_review.checkpointing import review_checkpoint_serializer
from agent_review.lifecycle import (
    ReviewEvent,
    ReviewState,
    apply_event,
    replay_review,
    start_review,
)
from agent_review.models import (
    EscalationSummary,
    Rebuttal,
    ReviewRequest,
    ReviewResponse,
)

_EVENT_MODELS = {
    "review_response": ReviewResponse,
    "rebuttal": Rebuttal,
    "escalation_summary": EscalationSummary,
}


class ReviewNotFound(KeyError):
    """Raised when a durable review identifier does not exist."""


class PersistenceConflict(ValueError):
    """Raised when an idempotency key is reused for different data."""


@contextmanager
def sqlite_review_checkpointer(
    database_path: Path,
) -> Iterator[SqliteSaver]:
    """Open a SQLite LangGraph saver using the shared strict serializer."""

    connection = sqlite3.connect(database_path, check_same_thread=False)
    try:
        yield SqliteSaver(
            connection,
            serde=review_checkpoint_serializer(),
        )
    finally:
        connection.close()


@dataclass(frozen=True)
class SqliteReviewStore:
    """Structured event source for canonical review-state replay."""

    database_path: Path

    def __post_init__(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reviews (
                    thread_id TEXT PRIMARY KEY,
                    request_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_events (
                    thread_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (thread_id, sequence),
                    UNIQUE (thread_id, event_id),
                    FOREIGN KEY (thread_id) REFERENCES reviews(thread_id)
                );
                """
            )

    def create_review(
        self,
        thread_id: str,
        request: ReviewRequest,
    ) -> ReviewState:
        """Persist a new review request and return its initial state."""

        self._validate_identifier(thread_id, "thread ID")
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO reviews(thread_id, request_json) VALUES (?, ?)",
                    (thread_id, request.model_dump_json()),
                )
            except sqlite3.IntegrityError as error:
                existing = connection.execute(
                    "SELECT request_json FROM reviews WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                if (
                    existing is not None
                    and ReviewRequest.model_validate_json(existing[0]) == request
                ):
                    return self._load_review(connection, thread_id)
                raise PersistenceConflict(
                    f"review {thread_id!r} already exists"
                ) from error
        return start_review(request)

    def load_review(self, thread_id: str) -> ReviewState:
        """Replay persisted events into canonical review state."""

        self._validate_identifier(thread_id, "thread ID")
        with self._connect() as connection:
            return self._load_review(connection, thread_id)

    def append_event(
        self,
        thread_id: str,
        event_id: str,
        event: ReviewEvent,
    ) -> ReviewState:
        """Validate and durably append one idempotent protocol event."""

        self._validate_identifier(thread_id, "thread ID")
        self._validate_identifier(event_id, "event ID")
        event_type, payload_json = self._serialize_event(event)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._load_review(connection, thread_id)
            existing = connection.execute(
                """
                SELECT event_type, payload_json
                FROM review_events
                WHERE thread_id = ? AND event_id = ?
                """,
                (thread_id, event_id),
            ).fetchone()
            if existing is not None:
                if existing == (event_type, payload_json):
                    return state
                raise PersistenceConflict(
                    f"event ID {event_id!r} was reused with different data"
                )

            next_state = apply_event(state, event)
            sequence = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM review_events
                WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO review_events(
                    thread_id, sequence, event_id, event_type, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, sequence, event_id, event_type, payload_json),
            )
            return next_state

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _load_review(
        self,
        connection: sqlite3.Connection,
        thread_id: str,
    ) -> ReviewState:
        request_row = connection.execute(
            "SELECT request_json FROM reviews WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        if request_row is None:
            raise ReviewNotFound(thread_id)
        event_rows = connection.execute(
            """
            SELECT event_type, payload_json
            FROM review_events
            WHERE thread_id = ?
            ORDER BY sequence
            """,
            (thread_id,),
        ).fetchall()
        events = tuple(
            self._deserialize_event(event_type, payload_json)
            for event_type, payload_json in event_rows
        )
        request = ReviewRequest.model_validate_json(request_row[0])
        return replay_review(request, events)

    @staticmethod
    def _serialize_event(event: ReviewEvent) -> tuple[str, str]:
        if isinstance(event, ReviewResponse):
            event_type = "review_response"
        elif isinstance(event, Rebuttal):
            event_type = "rebuttal"
        elif isinstance(event, EscalationSummary):
            event_type = "escalation_summary"
        else:
            raise TypeError(f"unsupported event type: {type(event).__name__}")
        return event_type, event.model_dump_json()

    @staticmethod
    def _deserialize_event(event_type: str, payload_json: str) -> ReviewEvent:
        model = _EVENT_MODELS.get(event_type)
        if model is None:
            raise ValueError(f"unknown persisted event type: {event_type!r}")
        return model.model_validate_json(payload_json)

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        if not value or len(value) > 255:
            raise ValueError(f"{name} must contain 1-255 characters")
