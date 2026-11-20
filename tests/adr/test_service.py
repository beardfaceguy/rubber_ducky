from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
from threading import Barrier

import pytest

from rubber_ducky.adr.models import (
    AdrRebuttal,
    AdrRebuttalInput,
    AdrEvidenceReference,
    AdrReviewRequest,
    AdrStartInput,
)
from rubber_ducky.adr.service import AdrReviewService
from rubber_ducky.adr.service import UnsupportedOperation
from rubber_ducky.code.models import Rebuttal as CodeRebuttal
from rubber_ducky.core.audit import ArtifactConflict, AuditLog
from rubber_ducky.core.lifecycle import ReviewStatus
from rubber_ducky.core.models import (
    BlockingConcernResponse,
    Disposition,
    Position,
    RebuttalRequest,
    Concern,
    ConcernKind,
    ReviewResponse,
    Verdict,
)
from rubber_ducky.core.reviewer_config import ReviewerModelConfig
from tests.adr.support import adr_markdown, evidence_json as make_evidence_json


def test_service_starts_review_with_exact_evidence_artifact(tmp_path: Path) -> None:
    markdown = adr_markdown()
    evidence_json = make_evidence_json(markdown)
    request = AdrReviewRequest(
        task_id="INFRA-108",
        title="Add ADR review mode",
        proposed_solution="Reuse protocol v1.3.",
        adr_markdown=markdown,
        adr_sha256=sha256(markdown.encode()).hexdigest(),
        repository_revision="abc123",
        evidence=AdrEvidenceReference(
            schema_version="1.0",
            relative_path=(
                "INFRA-108-adr-review/artifacts/"
                "round-1-request-evidence.json"
            ),
            sha256=sha256(evidence_json.encode()).hexdigest(),
        ),
    )
    service = AdrReviewService(tmp_path)

    state = service.start_with_evidence(
        "review-1",
        "adr-review",
        AdrStartInput(request=request, evidence_json=evidence_json),
    )

    assert state.status is ReviewStatus.AWAITING_REVIEW_RESPONSE
    evidence_path = (
        tmp_path
        / "rubber_ducky"
        / "INFRA-108-adr-review"
        / "artifacts"
        / "round-1-request-evidence.json"
    )
    assert evidence_path.read_bytes() == evidence_json.encode()

    evidence_path.write_text("tampered")
    with pytest.raises(ArtifactConflict, match="failed verification"):
        service.status("review-1")


def test_plain_start_is_rejected_without_evidence(tmp_path: Path) -> None:
    service = AdrReviewService(tmp_path)

    with pytest.raises(UnsupportedOperation, match="start_with_evidence"):
        service.start("review-1", "adr-review", object())  # type: ignore[arg-type]


def test_start_rejects_non_generated_evidence_path_before_artifact_write(
    tmp_path: Path,
) -> None:
    markdown = adr_markdown()
    evidence_json = make_evidence_json(markdown)
    request = AdrReviewRequest(
        task_id="INFRA-108",
        title="Add ADR review mode",
        proposed_solution="Reuse protocol v1.3.",
        adr_markdown=markdown,
        adr_sha256=sha256(markdown.encode()).hexdigest(),
        repository_revision="abc123",
        evidence=AdrEvidenceReference(
            schema_version="1.0",
            relative_path="wrong/artifacts/evidence.json",
            sha256=sha256(evidence_json.encode()).hexdigest(),
        ),
    )
    service = AdrReviewService(tmp_path)

    with pytest.raises(ValueError, match="service-generated"):
        service.start_with_evidence(
            "review-1",
            "adr-review",
            AdrStartInput(request=request, evidence_json=evidence_json),
        )

    assert not tuple((tmp_path / "rubber_ducky").glob("**/artifacts/*"))


def test_start_rejects_evidence_hash_before_artifact_write(tmp_path: Path) -> None:
    markdown = adr_markdown()
    evidence_json = make_evidence_json(markdown)
    request = AdrReviewRequest(
        task_id="INFRA-108",
        title="Add ADR review mode",
        proposed_solution="Reuse protocol v1.3.",
        adr_markdown=markdown,
        adr_sha256=sha256(markdown.encode()).hexdigest(),
        repository_revision="abc123",
        evidence=AdrEvidenceReference(
            schema_version="1.0",
            relative_path=(
                "INFRA-108-adr-review/artifacts/"
                "round-1-request-evidence.json"
            ),
            sha256="0" * 64,
        ),
    )
    service = AdrReviewService(tmp_path)

    with pytest.raises(ArtifactConflict, match="exact bytes"):
        service.start_with_evidence(
            "review-1",
            "adr-review",
            AdrStartInput(request=request, evidence_json=evidence_json),
        )

    assert not tuple((tmp_path / "rubber_ducky").glob("**/artifacts/*"))


def test_start_rejects_unknown_or_private_evidence_fields(tmp_path: Path) -> None:
    markdown = adr_markdown()
    evidence = json.loads(make_evidence_json(markdown))
    evidence["human_acceptance"] = "Accepted"
    evidence_json = json.dumps(evidence)
    request = AdrReviewRequest(
        task_id="INFRA-108",
        title="Add ADR review mode",
        proposed_solution="Reuse protocol v1.3.",
        adr_markdown=markdown,
        adr_sha256=sha256(markdown.encode()).hexdigest(),
        repository_revision="abc123",
        evidence=AdrEvidenceReference(
            schema_version="1.0",
            relative_path=(
                "INFRA-108-adr-review/artifacts/"
                "round-1-request-evidence.json"
            ),
            sha256=sha256(evidence_json.encode()).hexdigest(),
        ),
    )

    with pytest.raises(ValueError, match="unknown sections"):
        AdrReviewService(tmp_path).start_with_evidence(
            "review-1",
            "adr-review",
            AdrStartInput(request=request, evidence_json=evidence_json),
        )


def test_generic_submit_rejects_adr_rebuttal(tmp_path: Path) -> None:
    service = AdrReviewService(tmp_path)
    rebuttal = AdrRebuttal(
        round=1,
        position=Position.DISAGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.DISPUTE,
                reason="The concern does not apply.",
            ),
        ),
        requesting=RebuttalRequest.RE_REVIEW,
        repository_revision="abc123",
    )

    with pytest.raises(UnsupportedOperation, match="submit_adr_rebuttal"):
        service.submit("review-1", "event-1", rebuttal)


def test_generic_submit_rejects_other_mode_rebuttal(tmp_path: Path) -> None:
    service = AdrReviewService(tmp_path)
    rebuttal = CodeRebuttal(
        round=1,
        position=Position.DISAGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.DISPUTE,
                reason="Wrong review mode.",
            ),
        ),
        revised_diff="Unchanged — see Review Request.",
        requesting=RebuttalRequest.RE_REVIEW,
    )

    with pytest.raises(UnsupportedOperation, match="submit_adr_rebuttal"):
        service.submit("review-1", "event-1", rebuttal)


def test_automatic_reviewer_generation_is_rejected(tmp_path: Path) -> None:
    service = AdrReviewService(tmp_path)

    with pytest.raises(UnsupportedOperation, match="not supported"):
        service.generate_review(
            "review-1",
            "event-1",
            ReviewerModelConfig(provider="openai", model="test-model"),
        )


def test_service_stores_changed_rebuttal_evidence_before_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = adr_markdown()
    initial_evidence = make_evidence_json(original)
    service = AdrReviewService(tmp_path)
    service.start_with_evidence(
        "review-1",
        "adr-review",
        AdrStartInput(
            request=AdrReviewRequest(
                task_id="INFRA-108",
                title="Add ADR review mode",
                proposed_solution="Reuse protocol v1.3.",
                adr_markdown=original,
                adr_sha256=sha256(original.encode()).hexdigest(),
                repository_revision="abc123",
                evidence=AdrEvidenceReference(
                    schema_version="1.0",
                    relative_path=(
                        "INFRA-108-adr-review/artifacts/"
                        "round-1-request-evidence.json"
                    ),
                    sha256=sha256(initial_evidence.encode()).hexdigest(),
                ),
            ),
            evidence_json=initial_evidence,
        ),
    )
    service.submit(
        "review-1",
        "response-1",
        ReviewResponse(
            round=1,
            position=Position.PARTIAL,
            blocking_concerns=(
                Concern(
                    id="B1",
                    kind=ConcernKind.BLOCKING,
                    text="Clarify the evidence boundary.",
                ),
            ),
            verdict=Verdict.REVISE,
        ),
    )
    revised = adr_markdown(title="Decide clearly")
    revised_evidence = make_evidence_json(revised)
    rebuttal = AdrRebuttal(
        round=1,
        position=Position.AGREE,
        blocking_responses=(
            BlockingConcernResponse(
                concern_id="B1",
                disposition=Disposition.ACCEPT,
                reason="The boundary was ambiguous.",
            ),
        ),
        requesting=RebuttalRequest.RE_REVIEW,
        revised_adr_markdown=revised,
        revised_adr_sha256=sha256(revised.encode()).hexdigest(),
        repository_revision="abc123",
        revised_evidence=AdrEvidenceReference(
            schema_version="1.0",
            relative_path=(
                "INFRA-108-adr-review/artifacts/"
                "round-1-rebuttal-1-evidence.json"
            ),
            sha256=sha256(revised_evidence.encode()).hexdigest(),
        ),
    )

    original_append = AuditLog.append_prepared
    failed = False

    def fail_once(self, event, **kwargs):
        nonlocal failed
        if kwargs.get("event_id") == "rebuttal-1" and not failed:
            failed = True
            raise OSError("simulated audit failure")
        return original_append(self, event, **kwargs)

    monkeypatch.setattr(AuditLog, "append_prepared", fail_once)
    with pytest.raises(OSError, match="simulated audit failure"):
        service.submit_adr_rebuttal(
            "review-1",
            "rebuttal-1",
            AdrRebuttalInput(rebuttal=rebuttal, evidence_json=revised_evidence),
        )
    assert service.store.load_history("review-1").events[-1].event == rebuttal
    assert service.store.is_event_audited("review-1", "rebuttal-1") is False

    artifact_dir = (
        tmp_path / "rubber_ducky" / "INFRA-108-adr-review" / "artifacts"
    )
    assert (artifact_dir / "round-1-rebuttal-1-evidence.json").read_text() == (
        revised_evidence
    )
    assert (artifact_dir / "round-1-rebuttal-1.md").read_text() == revised

    state = service.submit_adr_rebuttal(
        "review-1",
        "rebuttal-1",
        AdrRebuttalInput(rebuttal=rebuttal, evidence_json=revised_evidence),
    )

    assert state.status is ReviewStatus.AWAITING_REVIEW_RESPONSE
    assert service.store.is_event_audited("review-1", "rebuttal-1") is True


def test_concurrent_duplicate_start_recovers_same_review(tmp_path: Path) -> None:
    markdown = adr_markdown()
    evidence_json = make_evidence_json(markdown)
    start_input = AdrStartInput(
        request=AdrReviewRequest(
            task_id="INFRA-108",
            title="Add ADR review mode",
            proposed_solution="Reuse protocol v1.3.",
            adr_markdown=markdown,
            adr_sha256=sha256(markdown.encode()).hexdigest(),
            repository_revision="abc123",
            evidence=AdrEvidenceReference(
                schema_version="1.0",
                relative_path=(
                    "INFRA-108-adr-review/artifacts/"
                    "round-1-request-evidence.json"
                ),
                sha256=sha256(evidence_json.encode()).hexdigest(),
            ),
        ),
        evidence_json=evidence_json,
    )
    barrier = Barrier(2)

    def start() -> ReviewStatus:
        barrier.wait()
        return AdrReviewService(tmp_path).start_with_evidence(
            "review-1",
            "adr-review",
            start_input,
        ).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = tuple(executor.map(lambda _index: start(), range(2)))

    assert statuses == (
        ReviewStatus.AWAITING_REVIEW_RESPONSE,
        ReviewStatus.AWAITING_REVIEW_RESPONSE,
    )
