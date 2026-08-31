"""Code-review binding of the shared JSON command-line interface."""

from rubber_ducky.code.models import Rebuttal, ReviewRequest
from rubber_ducky.code.service import CodeReviewService
from rubber_ducky.core.cli import build_main

main = build_main(
    prog="rubber-ducky-code",
    request_model=ReviewRequest,
    rebuttal_model=Rebuttal,
    service_factory=CodeReviewService,
)


def run() -> None:
    raise SystemExit(main())
