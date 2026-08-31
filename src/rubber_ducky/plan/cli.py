"""Plan-review binding of the shared JSON command-line interface."""

from rubber_ducky.core.cli import build_main
from rubber_ducky.plan.models import PlanRebuttal, PlanReviewRequest
from rubber_ducky.plan.service import PlanReviewService

main = build_main(
    prog="rubber-ducky-plan",
    request_model=PlanReviewRequest,
    rebuttal_model=PlanRebuttal,
    service_factory=PlanReviewService,
)


def run() -> None:
    raise SystemExit(main())
