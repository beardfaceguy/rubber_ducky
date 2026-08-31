"""Durable SQLite event storage and LangGraph checkpointer factories."""

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from rubber_ducky.core.checkpointing import review_checkpoint_serializer
from rubber_ducky.core.lifecycle import (
    ReviewEvent,
    ReviewState,
    apply_event,
    replay_review,
    start_review,
)
from rubber_ducky.core.models import (
    EscalationSummary,
    RebuttalBase,
    ReviewRequestBase,
    ReviewResponse,
)

_ALLOWED_EVENT_METADATA = {
    "provider",
    "model",
    "validation_attempts",
    "validation_errors",
}


class ReviewNotFound(KeyError):
    """Raised when a durable review identifier does not exist."""


class PersistenceConflict(ValueError):
    """Raised when an idempotency key is reused for different data."""


@dataclass(frozen=True)
class StoredEvent:
    event_id: str
    event: ReviewEvent
    metadata: dict[str, str]


@dataclass(frozen=True)
class StoredReview:
    request: ReviewRequestBase
    audit_slug: str | None
    events: tuple[StoredEvent, ...]


@dataclass(frozen=True)
class EventAppendResult:
    state: ReviewState
    appended: bool


@contextmanager
def sqlite_review_checkpointer(
    database_path: Path,
    additional_types: Iterable[type] = (),
) -> Iterator[SqliteSaver]:
    """Open a SQLite LangGraph saver using the shared strict serializer.

    ``additional_types`` extends the trusted deserialization allowlist with a
    domain's concrete payload and state types.
    """

    connection = sqlite3.connect(database_path, check_same_thread=False)
    try:
        yield SqliteSaver(
            connection,
            serde=review_checkpoint_serializer(additional_types),
        )
    finally:
        connection.close()


@dataclass(frozen=True)
class SqliteReviewStore:
    """Structured event source for canonical review-state replay.

    ``request_model`` and ``rebuttal_model`` bind the domain-specific request
    and rebuttal types used for (de)serialization; the reviewer response and
    escalation summary are shared across all domains.
    """

    database_path: Path
    request_model: type[ReviewRequestBase] = ReviewRequestBase
    rebuttal_model: type[RebuttalBase] = RebuttalBase
    state_model: type[ReviewState] = ReviewState

    def _event_models(self) -> dict[str, type]:
        return {
            "review_response": ReviewResponse,
            "rebuttal": self.rebuttal_model,
            "escalation_summary": EscalationSummary,
        }

    def __post_init__(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reviews (
                    thread_id TEXT PRIMARY KEY,
                    request_json TEXT NOT NULL,
                    audit_slug TEXT
                );

                CREATE TABLE IF NOT EXISTS review_events (
                    thread_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (thread_id, sequence),
                    UNIQUE (thread_id, event_id),
                    FOREIGN KEY (thread_id) REFERENCES reviews(thread_id)
                );

                CREATE TABLE IF NOT EXISTS audited_events (
                    thread_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    PRIMARY KEY (thread_id, event_id),
                    FOREIGN KEY (thread_id) REFERENCES reviews(thread_id)
                );
                """
            )
            event_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(review_events)"
                ).fetchall()
            }
            if "metadata_json" not in event_columns:
                connection.execute(
                    """
                    ALTER TABLE review_events
                    ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'
                    """
                )

    def create_review(
        self,
        thread_id: str,
        request: ReviewRequestBase,
        audit_slug: str,
    ) -> ReviewState:
        """Persist a new review request and return its initial state."""

        self._validate_identifier(thread_id, "thread ID")
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO reviews(thread_id, request_json, audit_slug)
                    VALUES (?, ?, ?)
                    """,
                    (thread_id, request.model_dump_json(), audit_slug),
                )
            except sqlite3.IntegrityError as error:
                existing = connection.execute(
                    """
                    SELECT request_json, audit_slug
                    FROM reviews
                    WHERE thread_id = ?
                    """,
                    (thread_id,),
                ).fetchone()
                if (
                    existing is not None
                    and self.request_model.model_validate_json(existing[0]) == request
                    and existing[1] == audit_slug
                ):
                    return self._load_review(connection, thread_id)
                raise PersistenceConflict(
                    f"review {thread_id!r} already exists"
                ) from error
        return start_review(request, state_cls=self.state_model)

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
        metadata: Mapping[str, str] | None = None,
    ) -> ReviewState:
        """Validate and durably append one idempotent protocol event."""

        return self.append_event_once(
            thread_id,
            event_id,
            event,
            metadata=metadata,
        ).state

    def append_event_once(
        self,
        thread_id: str,
        event_id: str,
        event: ReviewEvent,
        metadata: Mapping[str, str] | None = None,
    ) -> EventAppendResult:
        """Append an event and report whether this call inserted it."""

        self._validate_identifier(thread_id, "thread ID")
        self._validate_identifier(event_id, "event ID")
        event_type, payload_json = self._serialize_event(event)
        metadata_values = self._validate_metadata(dict(metadata or {}))
        metadata_json = json.dumps(metadata_values, sort_keys=True)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._load_review(connection, thread_id)
            existing = connection.execute(
                """
                SELECT event_type, payload_json, metadata_json
                FROM review_events
                WHERE thread_id = ? AND event_id = ?
                """,
                (thread_id, event_id),
            ).fetchone()
            if existing is not None:
                if existing == (event_type, payload_json, metadata_json):
                    return EventAppendResult(state=state, appended=False)
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
                    , metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    sequence,
                    event_id,
                    event_type,
                    payload_json,
                    metadata_json,
                ),
            )
            return EventAppendResult(state=next_state, appended=True)

    def load_history(self, thread_id: str) -> StoredReview:
        """Load validated request metadata and ordered protocol events."""

        self._validate_identifier(thread_id, "thread ID")
        with self._connect() as connection:
            return self._load_history(connection, thread_id)

    def is_event_audited(self, thread_id: str, event_id: str) -> bool:
        """Return whether the audit projection committed an event."""

        self._validate_identifier(thread_id, "thread ID")
        self._validate_identifier(event_id, "event ID")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM audited_events
                WHERE thread_id = ? AND event_id = ?
                """,
                (thread_id, event_id),
            ).fetchone()
        return row is not None

    def mark_event_audited(self, thread_id: str, event_id: str) -> None:
        """Idempotently record a completed audit-log projection."""

        self._validate_identifier(thread_id, "thread ID")
        self._validate_identifier(event_id, "event ID")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO audited_events(thread_id, event_id)
                VALUES (?, ?)
                """,
                (thread_id, event_id),
            )

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
        history = self._load_history(connection, thread_id)
        return replay_review(
            history.request,
            tuple(stored.event for stored in history.events),
            state_cls=self.state_model,
        )

    def _load_history(
        self,
        connection: sqlite3.Connection,
        thread_id: str,
    ) -> StoredReview:
        request_row = connection.execute(
            """
            SELECT request_json, audit_slug
            FROM reviews
            WHERE thread_id = ?
            """,
            (thread_id,),
        ).fetchone()
        if request_row is None:
            raise ReviewNotFound(thread_id)
        event_rows = connection.execute(
            """
            SELECT event_id, event_type, payload_json, metadata_json
            FROM review_events
            WHERE thread_id = ?
            ORDER BY sequence
            """,
            (thread_id,),
        ).fetchall()
        stored_events = tuple(
            StoredEvent(
                event_id=event_id,
                event=self._deserialize_event(event_type, payload_json),
                metadata=self._validate_metadata(json.loads(metadata_json)),
            )
            for event_id, event_type, payload_json, metadata_json in event_rows
        )
        request = self.request_model.model_validate_json(request_row[0])
        return StoredReview(
            request=request,
            audit_slug=request_row[1],
            events=stored_events,
        )

    @staticmethod
    def _serialize_event(event: ReviewEvent) -> tuple[str, str]:
        if isinstance(event, ReviewResponse):
            event_type = "review_response"
        elif isinstance(event, RebuttalBase):
            event_type = "rebuttal"
        elif isinstance(event, EscalationSummary):
            event_type = "escalation_summary"
        else:
            raise TypeError(f"unsupported event type: {type(event).__name__}")
        return event_type, event.model_dump_json()

    def _deserialize_event(self, event_type: str, payload_json: str) -> ReviewEvent:
        model = self._event_models().get(event_type)
        if model is None:
            raise ValueError(f"unknown persisted event type: {event_type!r}")
        return model.model_validate_json(payload_json)

    @staticmethod
    def _validate_metadata(value: object) -> dict[str, str]:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in value.items()
        ):
            raise ValueError("event metadata must be a string object")
        unknown_metadata = set(value) - _ALLOWED_EVENT_METADATA
        if unknown_metadata:
            raise ValueError(f"unsupported event metadata: {sorted(unknown_metadata)}")
        if any(not item for item in value.values()):
            raise ValueError("event metadata values cannot be empty")
        return value

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        if not value or len(value) > 255:
            raise ValueError(f"{name} must contain 1-255 characters")
