# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja #7 — add durable persistence and human resume
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Use SQLite for two complementary durable mechanisms: LangGraph checkpoints
resume pending interrupts after process restart, while a structured idempotent
event journal remains the canonical source for `replay_review`. Requests and
events are Pydantic JSON, event types are explicit, appends run under
`BEGIN IMMEDIATE`, and duplicate event keys are accepted only for identical
payloads. The shared strict serializer configures the SQLite checkpointer.

### Relevant Code / Diff
New file `src/agent_review/persistence.py`:

```python
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
                    return start_review(request)
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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

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
```

Dependency change:

```diff
+    "langgraph-checkpoint-sqlite>=3.1.1",
```

The ten complete tests in `tests/test_persistence.py` cover store reopening,
idempotent and conflicting review creation, durable replay, identical and
conflicting event keys, invalid-event rollback, pending-interrupt restart,
unknown-type rejection, and a multi-round history containing every event type.

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ LANGGRAPH_STRICT_MSGPACK=true uv run pytest -q
........................................................................ [ 76%]
......................                                                   [100%]
94 passed in 0.21s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### Known Concerns
1. Event journal and LangGraph checkpoint commits are separate transactions.
   The application service must append the idempotent event first, then resume
   the graph, so retries recover from a crash between them.
2. SQLite is the supported local backend; PostgreSQL can reuse the serializer
   and event-store contract later.
3. Identifier validation enforces only non-empty length bounds because IDs are
   database keys, not filesystem paths.

### Specific Questions for Reviewer
1. Does event replay remain the canonical state source after restart?
2. Are transactions and idempotency semantics safe under concurrent retries?
3. Does the SQLite checkpointer correctly reuse the centralized strict
   serializer and resume human interrupts after connection replacement?

## Review Response — Round 1
**Position:** AGREE

### Blocking Concerns
None.

### Non-Blocking Suggestions
1. **S1 — `_connect()` connections are never closed.** `sqlite3.Connection` used as a context manager only commits or rolls back on exit; it does not close. Every call to `create_review`, `load_review`, and `append_event` therefore leaves the connection open until garbage collection (`src/agent_review/persistence.py` lines 93, 118, 133, 170-173). CPython's refcounting closes them promptly in practice, so this is not observable today, but the durability layer should not rely on GC timing for file-handle release — wrapping the connection in `contextlib.closing` (or a small `@contextmanager` helper) makes cleanup deterministic in three lines.
2. **S2 — duplicate `create_review` returns the initial state, not the current state.** The idempotent path at lines 104-108 returns `start_review(request)` even when events have already been appended to that thread. A caller retrying `create_review` after a crash that occurred *after* events were journaled receives `AWAITING_REVIEW_RESPONSE` instead of the replayed state and could act on it. Returning `self._load_review(connection, thread_id)` on the duplicate path would make the retry return truth; alternatively, document that duplicate creation returns the as-if-new state and callers must `load_review` before acting.
3. **S3 — the concurrency claims are argued, not tested.** The `BEGIN IMMEDIATE` check-then-insert design is correct by construction (the immediate lock serializes writers, and Python's default five-second busy timeout handles contention), but no test exercises concurrent appends or concurrent duplicate creation. A small threaded test appending the same and different event IDs from two threads would lock in the semantics your Known Concern 1 recovery story depends on.

### Responses to Prior Points
(Not applicable in round 1. Answers to the Specific Questions follow.)

1. **Yes.** `load_review` reconstructs state exclusively by replaying journaled events through `replay_review`, which routes every event through the same `apply_event` validation used at append time (`lifecycle.py` lines 266-292). The LangGraph checkpoint is never consulted for canonical state, and `test_persisted_event_replays_after_reopen` plus `test_multi_round_history_replays_every_event_type` prove replay across a fresh store instance for every event type, terminal statuses included.
2. **Yes, with the caveats in S2 and S3.** `append_event` takes `BEGIN IMMEDIATE` before the existence check, so the check-then-insert sequence cannot race another writer; the duplicate path compares the exact serialized `(event_type, payload_json)` pair and returns replayed state for identical retries while raising `PersistenceConflict` for divergent reuse, and a failed `apply_event` rolls back before any insert (verified by `test_invalid_event_rolls_back_idempotency_key`). `create_review` is safe under concurrent duplicates because the primary key rejects the loser, and the losing insert can only observe committed rows when reading back for comparison. The one semantic wrinkle is S2.
3. **Yes.** `sqlite_review_checkpointer` passes `review_checkpoint_serializer()` — the same `JsonPlusSerializer` with the explicit `allowed_msgpack_modules` trust list from `checkpointing.py` — as `serde` to `SqliteSaver`, and `test_langgraph_interrupt_resumes_after_process_reopen` closes the first connection entirely, reopens the database, and resumes the pending interrupt to `APPROVED`. I reproduced the full suite locally with `LANGGRAPH_STRICT_MSGPACK=true`: 94 passed. The lock file resolves `langgraph-checkpoint-sqlite` to exactly 3.1.1.

Regarding Known Concern 1: the journal-first-then-resume ordering is a reasonable contract for this slice, but it is currently enforced only by documentation. When the application service slice lands, that ordering should live in one function so callers cannot invert it — flagging now so it is not lost, not blocking here.

**Verdict:** APPROVE

## Post-Approval Follow-Up
All three suggestions were adopted:

- S1: every store connection now commits or rolls back and then closes
  deterministically.
- S2: duplicate creation replays and returns current canonical state.
- S3: threaded tests verify identical concurrent events are idempotent and
  distinct concurrent events cannot advance the same round twice.

Ruff passes, 97 tests pass under strict checkpoint mode, and the package builds
successfully. Journal-first-then-resume ordering is carried into the application
service slice.
