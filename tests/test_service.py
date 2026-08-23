from pathlib import Path

import pytest

from agent_review.audit import AuditLog
from agent_review.lifecycle import ReviewStatus
from agent_review.models import ReviewRequest, ReviewResponse
from agent_review.persistence import PersistenceConflict, ReviewNotFound
from agent_review.service import ReviewService


def make_request() -> ReviewRequest:
    return ReviewRequest(
        task_id="AR-7",
        title="Expose application service",
        proposed_solution="Persist, audit, then resume the workflow.",
        relevant_diff="+service = ReviewService(workspace)",
    )


def test_service_starts_durable_review_and_audit(tmp_path: Path) -> None:
    service = ReviewService(tmp_path)

    state = service.start("review-1", "application-service", make_request())

    assert state.status is ReviewStatus.AWAITING_REVIEW_RESPONSE
    log = (tmp_path / "agent_review" / "AR-7-application-service.md").read_text(
        encoding="utf-8"
    )
    assert '<!-- event id="request" artifact ' in log
    assert "## Review Request — Round 1" in log


def test_service_start_is_idempotent(tmp_path: Path) -> None:
    service = ReviewService(tmp_path)
    first = service.start("review-1", "application-service", make_request())
    second = service.start("review-1", "application-service", make_request())

    assert second == first
    log = (tmp_path / "agent_review" / "AR-7-application-service.md").read_text(
        encoding="utf-8"
    )
    assert log.count("## Review Request — Round 1") == 1


def test_service_journals_audits_and_resumes_event(tmp_path: Path) -> None:
    service = ReviewService(tmp_path)
    service.start("review-1", "application-service", make_request())
    approval = ReviewResponse(
        round=1,
        position="AGREE",
        verdict="APPROVE",
    )

    completed = service.submit("review-1", "event-1", approval)
    reopened = ReviewService(tmp_path).status("review-1")

    assert completed.status is ReviewStatus.APPROVED
    assert reopened == completed
    log = (tmp_path / "agent_review" / "AR-7-application-service.md").read_text(
        encoding="utf-8"
    )
    assert '<!-- event id="event-1" -->' in log
    assert "## Review Response — Round 1" in log


def test_service_retry_recovers_crash_after_journal_commit(tmp_path: Path) -> None:
    service = ReviewService(tmp_path)
    service.start("review-1", "application-service", make_request())
    approval = ReviewResponse(
        round=1,
        position="AGREE",
        verdict="APPROVE",
    )
    service.store.append_event_once("review-1", "event-1", approval)

    recovered = ReviewService(tmp_path).submit(
        "review-1",
        "event-1",
        approval,
    )

    assert recovered.status is ReviewStatus.APPROVED
    log = (tmp_path / "agent_review" / "AR-7-application-service.md").read_text(
        encoding="utf-8"
    )
    assert log.count('event id="event-1"') == 1


def test_service_retry_skips_event_already_applied_to_graph(tmp_path: Path) -> None:
    service = ReviewService(tmp_path)
    service.start("review-1", "application-service", make_request())
    approval = ReviewResponse(
        round=1,
        position="AGREE",
        verdict="APPROVE",
    )
    service.store.append_event_once("review-1", "event-1", approval)
    assert service.status("review-1").status is ReviewStatus.APPROVED

    recovered = service.submit("review-1", "event-1", approval)

    assert recovered.status is ReviewStatus.APPROVED
    log = (tmp_path / "agent_review" / "AR-7-application-service.md").read_text(
        encoding="utf-8"
    )
    assert log.count('event id="event-1"') == 1


def test_reviewed_marker_text_cannot_suppress_audit_event(tmp_path: Path) -> None:
    service = ReviewService(tmp_path)
    request = make_request().model_copy(
        update={"relevant_diff": '<!-- event id="event-1" -->'}
    )
    service.start("review-1", "application-service", request)

    service.submit(
        "review-1",
        "event-1",
        ReviewResponse(round=1, position="AGREE", verdict="APPROVE"),
    )

    log = (tmp_path / "agent_review" / "AR-7-application-service.md").read_text(
        encoding="utf-8"
    )
    assert "## Review Response — Round 1" in log


def test_audit_projection_is_visible_at_least_once_after_crash(tmp_path: Path) -> None:
    service = ReviewService(tmp_path)
    service.start("review-1", "application-service", make_request())
    approval = ReviewResponse(round=1, position="AGREE", verdict="APPROVE")
    service.store.append_event_once("review-1", "event-1", approval)
    audit = AuditLog.open(
        tmp_path,
        "AR-7",
        "application-service",
        thread_id="review-1",
    )
    audit.append(approval, event_id="event-1")

    recovered = service.submit("review-1", "event-1", approval)

    assert recovered.status is ReviewStatus.APPROVED
    log = audit.log_path.read_text(encoding="utf-8")
    assert log.count("## Review Response — Round 1") == 2


def test_audit_path_cannot_be_shared_by_different_thread(tmp_path: Path) -> None:
    service = ReviewService(tmp_path)
    service.start("review-1", "application-service", make_request())

    with pytest.raises(PersistenceConflict, match="different thread"):
        service.start("review-2", "application-service", make_request())

    with pytest.raises(ReviewNotFound):
        service.store.load_review("review-2")


def test_start_recovers_orphaned_request_artifact(tmp_path: Path) -> None:
    service = ReviewService(tmp_path)
    request = make_request()
    service.store.create_review(
        "review-1",
        request,
        audit_slug="application-service",
    )
    audit = AuditLog.create(
        tmp_path,
        request.task_id,
        "application-service",
        thread_id="review-1",
    )
    (audit.artifacts_dir / "round-1-review-request.diff").write_text(
        request.relevant_diff,
        encoding="utf-8",
    )

    recovered = service.start(
        "review-1",
        "application-service",
        request,
    )

    assert recovered.status is ReviewStatus.AWAITING_REVIEW_RESPONSE
    assert "## Review Request — Round 1" in audit.log_path.read_text(encoding="utf-8")
