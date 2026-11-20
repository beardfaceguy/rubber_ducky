from hashlib import sha256

import pytest
from pydantic import ValidationError

from rubber_ducky.adr.models import (
    AdrEvidenceReference,
    AdrRebuttal,
    AdrReviewRequest,
)
from rubber_ducky.core.models import (
    BlockingConcernResponse,
    Disposition,
    Position,
    RebuttalRequest,
)
from tests.adr.support import adr_markdown


def test_adr_request_preserves_exact_markdown() -> None:
    markdown = adr_markdown(newline="\r\n", title="Keep exact bytes")
    request = AdrReviewRequest(
        task_id="INFRA-108",
        title="Add ADR review mode",
        proposed_solution="Reuse protocol v1.3.",
        adr_markdown=markdown,
        adr_sha256=sha256(markdown.encode()).hexdigest(),
        repository_revision="abc123",
        evidence=AdrEvidenceReference(
            schema_version="1.0",
            relative_path="INFRA-108-adr/artifacts/round-1-request-evidence.json",
            sha256="0" * 64,
        ),
    )

    assert request.payload_text() == markdown
    assert request.artifact_suffix() == "md"


@pytest.mark.parametrize(
    "markdown",
    [
        adr_markdown(title="Choose café handling"),
        adr_markdown(newline="\r\n", title="Choose café handling").rstrip("\r\n"),
    ],
)
def test_adr_request_preserves_unicode_and_final_newline(
    markdown: str,
) -> None:
    request = AdrReviewRequest(
        task_id="INFRA-108",
        title="Preserve exact bytes",
        proposed_solution="Do not normalize Markdown.",
        adr_markdown=markdown,
        adr_sha256=sha256(markdown.encode()).hexdigest(),
        repository_revision="abc123",
        evidence=AdrEvidenceReference(
            schema_version="1.0",
            relative_path="review/artifacts/evidence.json",
            sha256="0" * 64,
        ),
    )

    assert request.payload_text().encode() == markdown.encode()


def test_accepting_concern_requires_revised_adr_and_evidence() -> None:
    with pytest.raises(ValidationError, match="revised ADR and evidence"):
        AdrRebuttal(
            round=1,
            position=Position.AGREE,
            blocking_responses=(
                BlockingConcernResponse(
                    concern_id="B1",
                    disposition=Disposition.ACCEPT,
                    reason="The evidence gap is real.",
                ),
            ),
            requesting=RebuttalRequest.RE_REVIEW,
            revised_adr_markdown=None,
            revised_adr_sha256=None,
            repository_revision="abc123",
            revised_evidence=None,
        )


def test_adr_request_requires_exact_template_sections() -> None:
    markdown = "# Incomplete\n\nStatus: Proposed\n"

    with pytest.raises(ValidationError, match="missing ADR"):
        AdrReviewRequest(
            task_id="INFRA-108",
            title="Add ADR review mode",
            proposed_solution="Reuse protocol v1.3.",
            adr_markdown=markdown,
            adr_sha256=sha256(markdown.encode()).hexdigest(),
            repository_revision="abc123",
            evidence=AdrEvidenceReference(
                schema_version="1.0",
                relative_path="review/artifacts/evidence.json",
                sha256="0" * 64,
            ),
        )


def test_evidence_reference_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError, match="relative artifact path"):
        AdrEvidenceReference(
            schema_version="1.0",
            relative_path="../secret.json",
            sha256="0" * 64,
        )


def test_adr_request_rejects_multiple_status_lines() -> None:
    markdown = adr_markdown().replace(
        "Status: Proposed",
        "Status: Accepted\nStatus: Proposed",
    )

    with pytest.raises(ValidationError, match="exactly one Proposed"):
        AdrReviewRequest(
            task_id="INFRA-108",
            title="Add ADR review mode",
            proposed_solution="Reuse protocol v1.3.",
            adr_markdown=markdown,
            adr_sha256=sha256(markdown.encode()).hexdigest(),
            repository_revision="abc123",
            evidence=AdrEvidenceReference(
                schema_version="1.0",
                relative_path="review/artifacts/evidence.json",
                sha256="0" * 64,
            ),
        )
