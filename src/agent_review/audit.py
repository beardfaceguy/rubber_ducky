"""Append-only Markdown logs with local artifact tamper evidence.

Digests detect changes only while their trusted ``ArtifactEvidence`` is
available; the mutable Markdown log is not itself a cryptographic trust root.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest
from html import escape
from pathlib import Path

from agent_review.models import (
    UNCHANGED_DIFF,
    Concern,
    EscalationSummary,
    Rebuttal,
    ReviewRequest,
    ReviewResponse,
)

_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_EVENT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,254}")
_METADATA_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_LOG_HEADER = "# Agent Review Log\n**Protocol:** review-protocol.md v1.3\n"


def _validate_component(value: str, pattern: re.Pattern[str], name: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"invalid {name}: {value!r}")
    return value


class AuditIdentityConflict(ValueError):
    """Raised when a log path belongs to a different review thread."""


class ArtifactConflict(ValueError):
    """Raised when an existing artifact differs from the reviewed bytes."""


def _log_header(thread_id: str | None) -> str:
    if thread_id is None:
        return _LOG_HEADER
    safe_thread_id = _validate_component(
        thread_id,
        _EVENT_ID_PATTERN,
        "thread ID",
    )
    return f'{_LOG_HEADER}<!-- review thread_id="{safe_thread_id}" -->\n'


def _numbered(items: tuple[str, ...], empty: str) -> str:
    if not items:
        return empty
    return "\n".join(f"{number}. {item}" for number, item in enumerate(items, start=1))


def _render_request(request: ReviewRequest) -> str:
    return (
        "## Review Request — Round 1\n"
        f"**Task:** {request.task_id} — {request.title}\n"
        "**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.\n"
        "\n"
        "### Proposed Solution\n"
        f"{request.proposed_solution}\n"
        "\n"
        "### Relevant Code / Diff\n"
        f"{request.relevant_diff}\n"
        "\n"
        "### Known Concerns\n"
        f"{_numbered(request.known_concerns, 'None.')}\n"
        "\n"
        "### Specific Questions for Reviewer\n"
        f"{_numbered(request.questions, 'General review requested.')}\n"
    )


def _render_concerns(concerns: tuple[Concern, ...]) -> str:
    if not concerns:
        return "None."
    return "\n".join(f"{concern.id}. {concern.text}" for concern in concerns)


def _render_response(response: ReviewResponse) -> str:
    prior_lines = [
        *(
            f"Re {prior.point_id}: {prior.response}"
            for prior in response.prior_point_responses
        ),
        *(f"{concern_id}: resolved" for concern_id in response.resolved_concern_ids),
    ]
    prior_section = ""
    if response.round > 1:
        prior_text = "\n".join(prior_lines) if prior_lines else "None."
        prior_section = f"\n### Responses to Prior Points\n{prior_text}\n"
    return (
        f"## Review Response — Round {response.round}\n"
        f"**Position:** {response.position.value}\n"
        "\n"
        "### Blocking Concerns\n"
        f"{_render_concerns(response.blocking_concerns)}\n"
        "\n"
        "### Non-Blocking Suggestions\n"
        f"{_render_concerns(response.suggestions)}\n"
        f"{prior_section}"
        "\n"
        f"**Verdict:** {response.verdict.value}\n"
    )


def _render_rebuttal(rebuttal: Rebuttal) -> str:
    blocking_responses = "\n".join(
        f"Re {response.concern_id}: {response.disposition.value} — {response.reason}"
        for response in rebuttal.blocking_responses
    )
    new_points = (
        "\n".join(f"{point.id}. {point.text}" for point in rebuttal.new_points)
        if rebuttal.new_points
        else "None."
    )
    return (
        f"## Rebuttal — Round {rebuttal.round}\n"
        f"**Position:** {rebuttal.position.value}\n"
        "\n"
        "### Responses to Blocking Concerns\n"
        f"{blocking_responses}\n"
        "\n"
        "### Revised Code / Diff\n"
        f"{rebuttal.revised_diff}\n"
        "\n"
        "### New Points\n"
        f"{new_points}\n"
        "\n"
        f"**Requesting:** {rebuttal.requesting.value}\n"
    )


def _render_escalation(summary: EscalationSummary) -> str:
    concerns = "; ".join(
        f"{concern.concern_id}: {concern.status}"
        for concern in summary.unresolved_blocking_concerns
    )
    return (
        "## Escalation Summary\n"
        f"**Unresolved blocking concerns:** {concerns}\n"
        f"**Worker's final position:** {summary.worker_position}\n"
        f"**Reviewer's final position:** {summary.reviewer_position}\n"
        f"**Decision needed from operator:** {summary.decision_needed}\n"
    )


@dataclass(frozen=True)
class ArtifactEvidence:
    """Relative artifact location and digest recorded in the audit log."""

    relative_path: str
    sha256: str


@dataclass(frozen=True)
class AuditLog:
    """Paths and append operations for one review conversation."""

    workspace_root: Path
    thread_id: str | None
    task_id: str
    slug: str
    key: str
    log_path: Path
    artifacts_dir: Path

    @classmethod
    def create(
        cls,
        workspace_root: Path,
        task_id: str,
        slug: str,
        *,
        thread_id: str | None = None,
    ) -> "AuditLog":
        """Create a new review log and its private artifact directory.

        Distinct task/slug pairs that produce the same protocol filename
        intentionally collide rather than overwrite an existing review.
        """

        safe_task_id = _validate_component(task_id, _TASK_ID_PATTERN, "task ID")
        safe_slug = _validate_component(slug, _SLUG_PATTERN, "slug")
        if len(safe_slug) > 80:
            raise ValueError("slug must be at most 80 characters")

        key = f"{safe_task_id}-{safe_slug}"
        audit_root = workspace_root / "agent_review"
        artifacts_dir = audit_root / key / "artifacts"
        log_path = audit_root / f"{key}.md"

        audit_root.mkdir(parents=True, exist_ok=True)
        log_created = False
        try:
            with log_path.open(
                "x",
                encoding="utf-8",
                errors="strict",
            ) as log_file:
                log_created = True
                log_file.write(_log_header(thread_id))
            artifacts_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            if log_created:
                log_path.unlink(missing_ok=True)
            raise
        return cls(
            workspace_root=workspace_root,
            thread_id=thread_id,
            task_id=safe_task_id,
            slug=safe_slug,
            key=key,
            log_path=log_path,
            artifacts_dir=artifacts_dir,
        )

    @classmethod
    def open(
        cls,
        workspace_root: Path,
        task_id: str,
        slug: str,
        *,
        thread_id: str | None = None,
    ) -> "AuditLog":
        """Open an existing review after validating its paths and header."""

        safe_task_id = _validate_component(task_id, _TASK_ID_PATTERN, "task ID")
        safe_slug = _validate_component(slug, _SLUG_PATTERN, "slug")
        if len(safe_slug) > 80:
            raise ValueError("slug must be at most 80 characters")

        key = f"{safe_task_id}-{safe_slug}"
        audit_root = workspace_root / "agent_review"
        log_path = audit_root / f"{key}.md"
        artifacts_dir = audit_root / key / "artifacts"
        if (
            not log_path.is_file()
            or log_path.is_symlink()
            or not artifacts_dir.is_dir()
            or artifacts_dir.is_symlink()
        ):
            raise FileNotFoundError(f"review audit {key!r} is incomplete")
        log_text = log_path.read_text(encoding="utf-8")
        if not log_text.startswith(_LOG_HEADER):
            raise ValueError(f"review audit {key!r} has an invalid header")
        if thread_id is not None and not log_text.startswith(_log_header(thread_id)):
            raise AuditIdentityConflict(
                f"review audit {key!r} belongs to a different thread"
            )
        return cls(
            workspace_root=workspace_root,
            thread_id=thread_id,
            task_id=safe_task_id,
            slug=safe_slug,
            key=key,
            log_path=log_path,
            artifacts_dir=artifacts_dir,
        )

    def append(
        self,
        event: ReviewRequest | ReviewResponse | Rebuttal | EscalationSummary,
        *,
        event_id: str | None = None,
        audit_metadata: Mapping[str, str] | None = None,
    ) -> ArtifactEvidence | None:
        """Append one protocol message and return its artifact evidence."""

        if event_id is not None:
            safe_event_id = _validate_component(
                event_id,
                _EVENT_ID_PATTERN,
                "event ID",
            )
        else:
            safe_event_id = None

        if isinstance(event, ReviewResponse):
            metadata = self._metadata(
                safe_event_id,
                audit_metadata=audit_metadata,
            )
            self._append_text(f"\n{metadata}{_render_response(event)}")
            return None
        if isinstance(event, EscalationSummary):
            metadata = self._metadata(
                safe_event_id,
                audit_metadata=audit_metadata,
            )
            self._append_text(f"\n{metadata}{_render_escalation(event)}")
            return None

        if isinstance(event, Rebuttal):
            artifact_name = f"round-{event.round}-rebuttal.diff"
            artifact_text = event.revised_diff
            rendered_event = _render_rebuttal(event)
            if artifact_text == UNCHANGED_DIFF:
                metadata = self._metadata(
                    safe_event_id,
                    audit_metadata=audit_metadata,
                )
                self._append_text(f"\n{metadata}{rendered_event}")
                return None
        else:
            if event.task_id != self.task_id:
                raise ValueError(
                    f"request task {event.task_id!r} does not match audit task "
                    f"{self.task_id!r}"
                )
            artifact_name = "round-1-review-request.diff"
            artifact_text = event.relevant_diff
            rendered_event = _render_request(event)

        artifact_path = self.artifacts_dir / artifact_name
        artifact_bytes = artifact_text.encode("utf-8", errors="strict")
        artifact_created = False
        try:
            with artifact_path.open("xb") as artifact_file:
                artifact_file.write(artifact_bytes)
                artifact_created = True
        except FileExistsError as error:
            if artifact_path.read_bytes() != artifact_bytes:
                raise ArtifactConflict(
                    f"artifact {artifact_path.name!r} already exists with different bytes"
                ) from error

        evidence = ArtifactEvidence(
            relative_path=artifact_path.relative_to(self.log_path.parent).as_posix(),
            sha256=sha256(artifact_bytes).hexdigest(),
        )
        metadata = self._metadata(
            safe_event_id,
            evidence,
            audit_metadata,
        )
        try:
            self._append_text(f"\n{metadata}{rendered_event}")
        except Exception:
            if artifact_created:
                artifact_path.unlink(missing_ok=True)
            raise
        return evidence

    @staticmethod
    def _metadata(
        event_id: str | None,
        evidence: ArtifactEvidence | None = None,
        audit_metadata: Mapping[str, str] | None = None,
    ) -> str:
        attributes: list[str] = []
        if event_id is not None:
            attributes.append(f'event id="{event_id}"')
        if evidence is not None:
            attributes.extend(
                (
                    f'artifact path="{evidence.relative_path}"',
                    f'sha256="{evidence.sha256}"',
                )
            )
        for key, value in sorted((audit_metadata or {}).items()):
            safe_key = _validate_component(
                key,
                _METADATA_KEY_PATTERN,
                "audit metadata key",
            )
            safe_value = escape(value, quote=True).replace("--", "&#45;&#45;")
            attributes.append(f'{safe_key}="{safe_value}"')
        return f"<!-- {' '.join(attributes)} -->\n" if attributes else ""

    def _append_text(self, text: str) -> None:
        descriptor = os.open(self.log_path, os.O_WRONLY | os.O_APPEND)
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            errors="strict",
        ) as log_file:
            log_file.write(text)

    def verify_artifact(self, evidence: ArtifactEvidence) -> bool:
        """Return whether an artifact still matches its recorded digest."""

        artifact_path = (self.log_path.parent / evidence.relative_path).resolve()
        artifact_root = self.artifacts_dir.resolve()
        if not artifact_path.is_relative_to(artifact_root):
            raise ValueError("artifact path is outside this review")
        try:
            artifact_bytes = artifact_path.read_bytes()
        except FileNotFoundError:
            return False
        actual_digest = sha256(artifact_bytes).hexdigest()
        return compare_digest(actual_digest, evidence.sha256)
