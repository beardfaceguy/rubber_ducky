from rubber_ducky.code.models import (
    CODE_CHECKPOINT_TYPES,
    CodeReviewState,
    ReviewRequest,
)
from rubber_ducky.core.checkpointing import (
    review_checkpoint_serializer,
    review_checkpoint_types,
)
from rubber_ducky.core.lifecycle import start_review


def test_code_domain_types_extend_the_trusted_allowlist() -> None:
    trusted = set(review_checkpoint_types(CODE_CHECKPOINT_TYPES))

    assert set(CODE_CHECKPOINT_TYPES) <= trusted


def test_code_review_state_round_trips_through_the_code_serializer() -> None:
    serializer = review_checkpoint_serializer(CODE_CHECKPOINT_TYPES)
    original = start_review(
        ReviewRequest(
            task_id="AR-2",
            title="Round-trip the code domain",
            proposed_solution="Register concrete payloads explicitly.",
            relevant_diff="+state = CodeReviewState(...)",
        ),
        state_cls=CodeReviewState,
    )

    restored = serializer.loads_typed(serializer.dumps_typed(original))

    assert restored == original
    assert isinstance(restored, CodeReviewState)
    assert isinstance(restored.request, ReviewRequest)
