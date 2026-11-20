from hashlib import sha256

from rubber_ducky.adr.models import (
    ADR_CHECKPOINT_TYPES,
    AdrEvidenceReference,
    AdrReviewRequest,
    AdrReviewState,
)
from rubber_ducky.core.checkpointing import review_checkpoint_serializer
from rubber_ducky.core.lifecycle import start_review
from tests.adr.support import adr_markdown


def test_adr_state_round_trips_through_checkpoint_serializer() -> None:
    markdown = adr_markdown()
    request = AdrReviewRequest(
        task_id="INFRA-108",
        title="Add ADR review mode",
        proposed_solution="Reuse the shared core.",
        adr_markdown=markdown,
        adr_sha256=sha256(markdown.encode()).hexdigest(),
        repository_revision="abc123",
        evidence=AdrEvidenceReference(
            schema_version="1.0",
            relative_path="INFRA-108-adr/artifacts/round-1-request-evidence.json",
            sha256="0" * 64,
        ),
    )
    original = start_review(request, state_cls=AdrReviewState)
    serializer = review_checkpoint_serializer(ADR_CHECKPOINT_TYPES)

    restored = serializer.loads_typed(serializer.dumps_typed(original))

    assert restored == original
    assert isinstance(restored, AdrReviewState)
