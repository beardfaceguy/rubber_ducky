# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja 1361 — add simple artifact and audit logging
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Add a filesystem-only audit module that creates the existing protocol log path,
stores request and rebuttal diffs in an adjacent per-review artifact directory,
records SHA-256 evidence in HTML comments, and appends rendered protocol
messages. Exclusive file creation prevents application-level overwrites; digest
verification detects external tampering. Lifecycle ordering remains the
reducer's responsibility.

### Relevant Code / Diff
New file `src/agent_review/audit.py`:

```python
"""Append-only Markdown audit logs and reviewed artifact evidence."""

import os
import re
from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path

from agent_review.models import (
    Concern,
    EscalationSummary,
    Rebuttal,
    ReviewRequest,
    ReviewResponse,
)

_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _validate_component(value: str, pattern: re.Pattern[str], name: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"invalid {name}: {value!r}")
    return value


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
    concerns = "\n".join(
        f"{concern.concern_id}: {concern.status}"
        for concern in summary.unresolved_blocking_concerns
    )
    return (
        "## Escalation Summary\n"
        f"**Unresolved blocking concerns:**\n{concerns}\n"
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
    ) -> "AuditLog":
        """Create a new review log and its private artifact directory."""

        safe_task_id = _validate_component(task_id, _TASK_ID_PATTERN, "task ID")
        safe_slug = _validate_component(slug, _SLUG_PATTERN, "slug")
        if len(safe_slug) > 80:
            raise ValueError("slug must be at most 80 characters")

        key = f"{safe_task_id}-{safe_slug}"
        audit_root = workspace_root / "agent_review"
        artifacts_dir = audit_root / key / "artifacts"
        log_path = audit_root / f"{key}.md"

        artifacts_dir.mkdir(parents=True, exist_ok=True)
        with log_path.open(
            "x",
            encoding="utf-8",
            errors="strict",
        ) as log_file:
            log_file.write(
                "# Agent Review Log\n**Protocol:** review-protocol.md v1.3\n"
            )
        return cls(
            workspace_root=workspace_root,
            task_id=safe_task_id,
            slug=safe_slug,
            key=key,
            log_path=log_path,
            artifacts_dir=artifacts_dir,
        )

    def append(
        self,
        event: ReviewRequest | ReviewResponse | Rebuttal | EscalationSummary,
    ) -> ArtifactEvidence | None:
        """Append one protocol message and return its artifact evidence."""

        if isinstance(event, ReviewResponse):
            self._append_text(f"\n{_render_response(event)}")
            return None
        if isinstance(event, EscalationSummary):
            self._append_text(f"\n{_render_escalation(event)}")
            return None

        if isinstance(event, Rebuttal):
            artifact_name = f"round-{event.round}-rebuttal.diff"
            artifact_text = event.revised_diff
            rendered_event = _render_rebuttal(event)
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
        with artifact_path.open("xb") as artifact_file:
            artifact_file.write(artifact_bytes)

        evidence = ArtifactEvidence(
            relative_path=artifact_path.relative_to(self.log_path.parent).as_posix(),
            sha256=sha256(artifact_bytes).hexdigest(),
        )
        metadata = (
            f'<!-- artifact path="{evidence.relative_path}" '
            f'sha256="{evidence.sha256}" -->\n'
        )
        try:
            self._append_text(f"\n{metadata}{rendered_event}")
        except Exception:
            artifact_path.unlink(missing_ok=True)
            raise
        return evidence

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
```

The complete 13 observable-behavior tests are in `tests/test_audit.py`. They
cover traversal rejection, compatible paths, exclusive log creation, request
identity, immutable artifacts, rollback on append failure, hash verification,
verification containment, all four renderers, and append ordering. The reviewer
should inspect the actual test file alongside the implementation.

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ uv run pytest -q
.......................................................                  [100%]
55 passed in 0.06s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### Known Concerns
1. If the OS partially writes a log entry before raising, artifact rollback
   cannot roll back those partial log bytes.
2. This slice creates new logs but has no validated `open` operation for
   resuming an existing log; durable resume remains a later slice.
3. The audit writer accepts individually valid messages and does not duplicate
   lifecycle-order validation.

### Specific Questions for Reviewer
1. Does the layout preserve current protocol compatibility while keeping
   artifacts isolated per review?
2. Are overwrite, traversal, and tamper-evidence guarantees correctly scoped
   and enforced?
3. Does each renderer preserve all required protocol fields without inventing
   lifecycle behavior?

## Review Response — Round 1
**Position:** AGREE

### Blocking Concerns
None.

### Non-Blocking Suggestions
S1. (Re Q1) The layout is protocol-compatible — the log path matches the `agent_review/<task-id>-<short-slug>.md` naming in review-protocol.md v1.3 §Log File, and per-review artifacts are isolated under `<key>/artifacts/`. However, the composite key `f"{task_id}-{slug}"` is not injective because `_TASK_ID_PATTERN` permits hyphens: task `AR-3` + slug `audit-logging` and task `AR` + slug `3-audit-logging` both produce key `AR-3-audit-logging`. Exclusive creation converts the collision into `FileExistsError`, so nothing is overwritten, but the error misleadingly reports a duplicate review. Document the constraint or use an unambiguous separator.

S2. (Re Q2, scoping) A dispute/clarify-only rebuttal carries the sentinel `"Unchanged — see Review Request."` as `revised_diff` (enforced by `Rebuttal.accepted_concerns_include_revised_diff` in `models.py`), and `append` stores that sentinel verbatim as `round-N-rebuttal.diff` with SHA-256 evidence. The evidence then attests a placeholder string rather than reviewed code. Consider skipping artifact creation for the sentinel, since the reviewed diff for that round is the already-hashed round-1 artifact.

S3. (Re Q2, enforcement) Overwrite (`"x"`/`"xb"` opens), traversal (`_validate_component` on inputs, `is_relative_to` containment in `verify_artifact`, including absolute-path and symlink-escape cases via `resolve()`), and artifact tamper detection are correctly enforced and tested. But the guarantee is narrower than "detects external tampering" suggests: the log that records the digests is itself a mutable plain file, and there is no API yet to recover `ArtifactEvidence` from an existing log (the deferred `open` slice), so `verify_artifact` only works for the in-process instance holding the evidence object. State this scope in the module docstring so callers don't over-trust it.

S4. (Re Q3) All four renderers preserve the required protocol fields with correct stable-ID formats, and `_render_response` correctly omits "Responses to Prior Points" in round 1 without inventing lifecycle behavior. One cosmetic deviation: `_render_escalation` places the concern list on lines below `**Unresolved blocking concerns:**` instead of after the colon as the §Escalation report template shows. Since the protocol defines format deviation as itself a violation, matching the template exactly is worth the one-line change. Relatedly, `create` leaves an empty `<key>/artifacts/` directory behind when the log already exists, because `mkdir` precedes the exclusive open; reordering removes the residue.

**Verdict:** APPROVE

## Post-Approval Follow-Up
All four suggestions were adopted without changing lifecycle behavior:

- S1: documented composite-key collision behavior.
- S2: unchanged-diff rebuttals append no artifact or digest.
- S3: documented that the mutable log is not a cryptographic trust root.
- S4: matched the escalation template exactly and made failed creation remove
  no unrelated review artifacts.

Added regression tests for unchanged rebuttals and collision residue. Ruff
passes, 57 tests pass, and the package builds successfully.
