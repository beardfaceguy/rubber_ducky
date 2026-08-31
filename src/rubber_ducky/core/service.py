"""Durable application service coordinating journal, audit, and workflow."""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import ClassVar

from langgraph.types import Command

from rubber_ducky.core.adapters import ReviewerAdapter
from rubber_ducky.core.audit import ArtifactConflict, AuditIdentityConflict, AuditLog
from rubber_ducky.core.lifecycle import ReviewEvent, ReviewState, replay_review
from rubber_ducky.core.models import RebuttalBase, ReviewRequestBase
from rubber_ducky.core.persistence import (
    PersistenceConflict,
    SqliteReviewStore,
    StoredReview,
    sqlite_review_checkpointer,
)
from rubber_ducky.core.reviewer_config import ReviewerModelConfig, ReviewerModelFactory
from rubber_ducky.core.workflow import build_review_graph


@dataclass(frozen=True)
class ReviewService:
    """Coordinate durable review side effects in recoverable order.

    ``request_model`` and ``rebuttal_model`` bind the domain the service
    persists; subclasses override them to serve a different review domain.
    """

    workspace_root: Path

    request_model: ClassVar[type[ReviewRequestBase]] = ReviewRequestBase
    rebuttal_model: ClassVar[type[RebuttalBase]] = RebuttalBase
    state_model: ClassVar[type[ReviewState]] = ReviewState
    checkpoint_types: ClassVar[tuple[type, ...]] = ()

    def __post_init__(self) -> None:
        (self.workspace_root / "rubber_ducky").mkdir(parents=True, exist_ok=True)

    @property
    def database_path(self) -> Path:
        return self.workspace_root / "rubber_ducky" / "reviews.sqlite"

    @cached_property
    def store(self) -> SqliteReviewStore:
        return SqliteReviewStore(
            self.database_path,
            request_model=self.request_model,
            rebuttal_model=self.rebuttal_model,
            state_model=self.state_model,
        )

    def start(
        self,
        thread_id: str,
        slug: str,
        request: ReviewRequestBase,
    ) -> ReviewState:
        """Start or recover a review and its human-readable audit."""

        try:
            audit: AuditLog | None = AuditLog.open(
                self.workspace_root,
                request.task_id,
                slug,
                thread_id=thread_id,
            )
        except FileNotFoundError:
            audit = None
        except AuditIdentityConflict as error:
            raise PersistenceConflict(str(error)) from error

        self.store.create_review(
            thread_id,
            request,
            audit_slug=slug,
        )
        if audit is None:
            try:
                audit = AuditLog.create(
                    self.workspace_root,
                    request.task_id,
                    slug,
                    thread_id=thread_id,
                )
            except FileExistsError:
                try:
                    audit = AuditLog.open(
                        self.workspace_root,
                        request.task_id,
                        slug,
                        thread_id=thread_id,
                    )
                except AuditIdentityConflict as error:
                    raise PersistenceConflict(str(error)) from error
        if not self.store.is_event_audited(thread_id, "request"):
            try:
                audit.append(request, event_id="request")
            except ArtifactConflict as error:
                raise PersistenceConflict(str(error)) from error
            self.store.mark_event_audited(thread_id, "request")
        return self.status(thread_id)

    def status(self, thread_id: str) -> ReviewState:
        """Return canonical state, writing repairs to a lagging checkpoint."""

        history = self.store.load_history(thread_id)
        canonical = replay_review(
            history.request,
            tuple(stored.event for stored in history.events),
            state_cls=self.state_model,
        )
        self._reconcile_graph(thread_id, history, canonical)
        return canonical

    def submit(
        self,
        thread_id: str,
        event_id: str,
        event: ReviewEvent,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> ReviewState:
        """Journal, audit, then apply an event.

        Journal and graph state are idempotent. Markdown audit projection is
        at-least-once: a crash after append but before its SQLite marker may
        produce a visible duplicate entry on retry rather than lose evidence.
        """

        result = self.store.append_event_once(
            thread_id,
            event_id,
            event,
            metadata=metadata,
        )
        history = self.store.load_history(thread_id)
        audit = self._open_audit(thread_id, history)
        if result.appended or not self.store.is_event_audited(thread_id, event_id):
            try:
                audit.append(
                    event,
                    event_id=event_id,
                    audit_metadata={
                        f"reviewer.{key}": value
                        for key, value in (metadata or {}).items()
                    },
                )
            except ArtifactConflict as error:
                raise PersistenceConflict(str(error)) from error
            self.store.mark_event_audited(thread_id, event_id)
        self._reconcile_graph(thread_id, history, result.state)
        return result.state

    def generate_review(
        self,
        thread_id: str,
        event_id: str,
        config: ReviewerModelConfig,
        *,
        factory: ReviewerModelFactory | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> ReviewState:
        """Generate, validate, persist, and audit one reviewer response.

        Retrying a persisted event skips model invocation. Concurrent first
        attempts can both invoke the provider; event idempotency still permits
        only one result to become canonical.
        """

        config_metadata = config.audit_metadata()
        history = self.store.load_history(thread_id)
        for stored in history.events:
            if stored.event_id == event_id:
                stored_config = {
                    key: stored.metadata.get(key) for key in config_metadata
                }
                if stored_config != config_metadata:
                    raise PersistenceConflict(
                        f"event ID {event_id!r} was reused with different "
                        "reviewer configuration"
                    )
                return self.submit(
                    thread_id,
                    event_id,
                    stored.event,
                    metadata=stored.metadata,
                )

        state = self.status(thread_id)
        model = (factory or ReviewerModelFactory()).create(
            config,
            environment=environment,
        )
        generated = ReviewerAdapter(model).review_with_diagnostics(state)
        metadata = {
            **config_metadata,
            "validation_attempts": str(generated.attempts),
        }
        if generated.validation_errors:
            metadata["validation_errors"] = "\n\n".join(generated.validation_errors)[
                :2000
            ]
        return self.submit(
            thread_id,
            event_id,
            generated.response,
            metadata=metadata,
        )

    def _open_audit(self, thread_id: str, history: StoredReview) -> AuditLog:
        if history.audit_slug is None:
            raise PersistenceConflict("review has no persisted audit slug")
        try:
            return AuditLog.open(
                self.workspace_root,
                history.request.task_id,
                history.audit_slug,
                thread_id=thread_id,
            )
        except AuditIdentityConflict as error:
            raise PersistenceConflict(str(error)) from error

    def _reconcile_graph(
        self,
        thread_id: str,
        history: StoredReview,
        canonical: ReviewState,
    ) -> None:
        config = {"configurable": {"thread_id": thread_id}}
        events = tuple(stored.event for stored in history.events)
        with sqlite_review_checkpointer(
            self.database_path,
            self.checkpoint_types,
        ) as checkpointer:
            graph = build_review_graph(
                checkpointer,
                state_cls=self.state_model,
                additional_types=self.checkpoint_types,
            )
            snapshot = graph.get_state(config)
            checkpoint_review = snapshot.values.get("review")
            if checkpoint_review is None:
                graph.invoke({"request": history.request}, config)
                checkpoint_review = graph.get_state(config).values["review"]

            applied_events = (
                len(checkpoint_review.responses)
                + len(checkpoint_review.rebuttals)
                + (1 if checkpoint_review.escalation_summary is not None else 0)
            )
            if applied_events > len(events):
                raise PersistenceConflict("checkpoint is ahead of canonical journal")
            expected_checkpoint = replay_review(
                history.request,
                events[:applied_events],
                state_cls=self.state_model,
            )
            if checkpoint_review != expected_checkpoint:
                raise PersistenceConflict("checkpoint diverges from canonical journal")

            for event in events[applied_events:]:
                graph.invoke(Command(resume=event), config)

            final_review = graph.get_state(config).values["review"]
            if final_review != canonical:
                raise PersistenceConflict(
                    "checkpoint did not converge to canonical journal"
                )
