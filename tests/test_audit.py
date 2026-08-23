from hashlib import sha256
from pathlib import Path

import pytest

from agent_review.audit import ArtifactConflict, ArtifactEvidence, AuditLog
from agent_review.models import (
    BlockingConcernResponse,
    Concern,
    ConcernKind,
    Disposition,
    EscalationConcern,
    EscalationSummary,
    Position,
    PriorPointResponse,
    Rebuttal,
    RebuttalPoint,
    RebuttalRequest,
    ReviewRequest,
    ReviewResponse,
    Verdict,
)


def test_review_path_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AuditLog.create(tmp_path, task_id="../AR-3", slug="audit-logging")


def test_create_uses_protocol_compatible_layout(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")

    assert audit.log_path == tmp_path / "agent_review" / "AR-3-audit-logging.md"
    assert audit.artifacts_dir == (
        tmp_path / "agent_review" / "AR-3-audit-logging" / "artifacts"
    )
    assert audit.log_path.read_text(encoding="utf-8") == (
        "# Agent Review Log\n**Protocol:** review-protocol.md v1.3\n"
    )


def test_open_validates_existing_log_and_artifact_directory(tmp_path: Path) -> None:
    created = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")

    reopened = AuditLog.open(tmp_path, task_id="AR-3", slug="audit-logging")

    assert reopened == created


def test_create_never_overwrites_existing_log(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    audit.log_path.write_text("existing audit", encoding="utf-8")

    with pytest.raises(FileExistsError):
        AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")

    assert audit.log_path.read_text(encoding="utf-8") == "existing audit"


def test_create_collision_leaves_no_new_artifact_directory(tmp_path: Path) -> None:
    audit_root = tmp_path / "agent_review"
    audit_root.mkdir()
    (audit_root / "AR-3-audit-logging.md").write_text(
        "existing audit",
        encoding="utf-8",
    )

    with pytest.raises(FileExistsError):
        AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")

    assert not (audit_root / "AR-3-audit-logging").exists()


def test_append_request_records_immutable_artifact_and_hash(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    request = ReviewRequest(
        task_id="AR-3",
        title="Add audit logging",
        proposed_solution="Append protocol messages and hash reviewed diffs.",
        relevant_diff="+immutable = True",
        known_concerns=("Filesystem writes can fail.",),
        questions=("Is the artifact evidence sufficient?",),
    )

    evidence = audit.append(request)

    assert evidence is not None
    artifact_path = audit.log_path.parent / evidence.relative_path
    assert artifact_path.read_text(encoding="utf-8") == request.relevant_diff
    assert evidence.sha256 == sha256(request.relevant_diff.encode()).hexdigest()
    log = audit.log_path.read_text(encoding="utf-8")
    assert (
        f'<!-- artifact path="{evidence.relative_path}" sha256="{evidence.sha256}" -->'
    ) in log
    assert "## Review Request — Round 1" in log
    assert request.relevant_diff in log


def test_append_request_must_match_audit_task(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    wrong_task = ReviewRequest(
        task_id="AR-4",
        title="Different task",
        proposed_solution="Do something else.",
        relevant_diff="+wrong_task = True",
    )
    original_log = audit.log_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        audit.append(wrong_task)

    assert audit.log_path.read_text(encoding="utf-8") == original_log
    assert not any(audit.artifacts_dir.iterdir())


def test_append_never_overwrites_existing_artifact(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    request = ReviewRequest(
        task_id="AR-3",
        title="Add audit logging",
        proposed_solution="Append protocol messages.",
        relevant_diff="+immutable = True",
    )
    audit.append(request)
    original_log = audit.log_path.read_text(encoding="utf-8")
    conflicting = request.model_copy(update={"relevant_diff": "+changed = True"})

    with pytest.raises(ArtifactConflict):
        audit.append(conflicting)

    assert audit.log_path.read_text(encoding="utf-8") == original_log


def test_identical_orphaned_artifact_is_reused_after_crash(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    request = ReviewRequest(
        task_id="AR-3",
        title="Add audit logging",
        proposed_solution="Recover interrupted artifact writes.",
        relevant_diff="+immutable = True",
    )
    artifact_path = audit.artifacts_dir / "round-1-review-request.diff"
    artifact_path.write_text(request.relevant_diff, encoding="utf-8")

    evidence = audit.append(request, event_id="request")

    assert evidence is not None
    assert "## Review Request — Round 1" in audit.log_path.read_text(encoding="utf-8")


def test_failed_log_append_removes_uncommitted_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    request = ReviewRequest(
        task_id="AR-3",
        title="Add audit logging",
        proposed_solution="Append protocol messages.",
        relevant_diff="+immutable = True",
    )

    def fail_append(_audit: AuditLog, _text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(AuditLog, "_append_text", fail_append)

    with pytest.raises(OSError, match="disk full"):
        audit.append(request)

    assert not any(audit.artifacts_dir.iterdir())


def test_artifact_hash_detects_external_tampering(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    request = ReviewRequest(
        task_id="AR-3",
        title="Add audit logging",
        proposed_solution="Hash reviewed artifacts.",
        relevant_diff="+immutable = True",
    )
    evidence = audit.append(request)
    assert evidence is not None
    assert audit.verify_artifact(evidence)

    artifact_path = audit.log_path.parent / evidence.relative_path
    artifact_path.write_text("+tampered = True", encoding="utf-8")

    assert not audit.verify_artifact(evidence)


def test_artifact_verification_rejects_path_outside_review(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    outside = ArtifactEvidence(relative_path="../other.diff", sha256="0" * 64)

    with pytest.raises(ValueError):
        audit.verify_artifact(outside)


def test_append_response_renders_protocol_sections_without_artifact(
    tmp_path: Path,
) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    response = ReviewResponse(
        round=2,
        position=Position.PARTIAL,
        blocking_concerns=(
            Concern(id="B2", kind=ConcernKind.BLOCKING, text="New blocker."),
        ),
        suggestions=(
            Concern(id="S1", kind=ConcernKind.SUGGESTION, text="Optional cleanup."),
        ),
        resolved_concern_ids=("B1",),
        prior_point_responses=(
            PriorPointResponse(point_id="R1", response="The evidence was sufficient."),
        ),
        verdict=Verdict.REVISE,
    )

    evidence = audit.append(response)

    assert evidence is None
    log = audit.log_path.read_text(encoding="utf-8")
    assert "## Review Response — Round 2" in log
    assert "B2. New blocker." in log
    assert "S1. Optional cleanup." in log
    assert "Re R1: The evidence was sufficient." in log
    assert "B1: resolved" in log
    assert "<!-- artifact " not in log


def test_messages_are_appended_in_order(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    request = ReviewRequest(
        task_id="AR-3",
        title="Add audit logging",
        proposed_solution="Append messages.",
        relevant_diff="+append_only = True",
    )
    response = ReviewResponse(
        round=1,
        position=Position.AGREE,
        verdict=Verdict.APPROVE,
    )

    audit.append(request)
    audit.append(response)

    log = audit.log_path.read_text(encoding="utf-8")
    assert log.index("## Review Request") < log.index("## Review Response")
    assert log.count("# Agent Review Log") == 1


def test_event_id_is_rendered_as_metadata(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    response = ReviewResponse(
        round=1,
        position=Position.AGREE,
        verdict=Verdict.APPROVE,
    )

    audit.append(response, event_id="event-1")
    log = audit.log_path.read_text(encoding="utf-8")

    assert '<!-- event id="event-1" -->' in log


def test_append_rebuttal_records_revised_diff_artifact(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    rebuttal = Rebuttal(
        round=2,
        position=Position.PARTIAL,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.ACCEPT,
                reason="The race is real.",
            ),
        ),
        revised_diff="+lock.acquire()",
        new_points=(RebuttalPoint(id="R1", text="The lock is scoped narrowly."),),
        requesting=RebuttalRequest.RE_REVIEW,
    )

    evidence = audit.append(rebuttal)

    assert evidence is not None
    assert evidence.relative_path.endswith("round-2-rebuttal.diff")
    artifact_path = audit.log_path.parent / evidence.relative_path
    assert artifact_path.read_text(encoding="utf-8") == rebuttal.revised_diff
    log = audit.log_path.read_text(encoding="utf-8")
    assert "## Rebuttal — Round 2" in log
    assert "Re B1: ACCEPT — The race is real." in log
    assert "R1. The lock is scoped narrowly." in log
    assert "**Requesting:** RE-REVIEW" in log


def test_unchanged_rebuttal_does_not_create_artifact(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    rebuttal = Rebuttal(
        round=1,
        position=Position.DISAGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.DISPUTE,
                reason="The concern does not apply.",
            ),
        ),
        revised_diff="Unchanged — see Review Request.",
        requesting=RebuttalRequest.RE_REVIEW,
    )

    evidence = audit.append(rebuttal)

    assert evidence is None
    assert not any(audit.artifacts_dir.iterdir())
    assert "Unchanged — see Review Request." in audit.log_path.read_text(
        encoding="utf-8"
    )


def test_append_escalation_summary_renders_operator_decision(tmp_path: Path) -> None:
    audit = AuditLog.create(tmp_path, task_id="AR-3", slug="audit-logging")
    summary = EscalationSummary(
        unresolved_blocking_concerns=(
            EscalationConcern(concern_id="B1", status="Still disputed."),
            EscalationConcern(concern_id="B2", status="Evidence incomplete."),
        ),
        worker_position="The implementation is safe.",
        reviewer_position="The blockers remain.",
        decision_needed="Should implementation continue?",
    )

    evidence = audit.append(summary)

    assert evidence is None
    log = audit.log_path.read_text(encoding="utf-8")
    assert "## Escalation Summary" in log
    assert (
        "**Unresolved blocking concerns:** "
        "B1: Still disputed.; B2: Evidence incomplete."
    ) in log
    assert "**Worker's final position:** The implementation is safe." in log
    assert "**Reviewer's final position:** The blockers remain." in log
    assert "**Decision needed from operator:** Should implementation continue?" in log
