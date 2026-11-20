"""JSON CLI for durable ADR reviews."""

import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from rubber_ducky.adr.models import (
    AdrRebuttal,
    AdrRebuttalInput,
    AdrReviewRequest,
    AdrStartInput,
)
from rubber_ducky.adr.service import AdrReviewService
from rubber_ducky.core.audit import ArtifactConflict
from rubber_ducky.core.cli import (
    CliInputError,
    JsonArgumentParser,
    _emit,
    _read_model,
    _result,
)
from rubber_ducky.core.lifecycle import InvalidTransition
from rubber_ducky.core.models import EscalationSummary, ReviewResponse
from rubber_ducky.core.persistence import PersistenceConflict, ReviewNotFound


def _parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(prog="rubber-ducky-adr")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start")
    start.add_argument("thread_id")
    start.add_argument("slug")
    start.add_argument("--input", required=True)
    start.add_argument("--evidence", required=True)

    status = commands.add_parser("status")
    status.add_argument("thread_id")

    for name in ("respond", "resume"):
        event = commands.add_parser(name)
        event.add_argument("thread_id")
        event.add_argument("event_id")
        event.add_argument("--input", required=True)

    rebut = commands.add_parser("rebut")
    rebut.add_argument("thread_id")
    rebut.add_argument("event_id")
    rebut.add_argument("--input", required=True)
    rebut.add_argument("--evidence")
    return parser


def _read_exact(path: str) -> str:
    try:
        return Path(path).read_bytes().decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as error:
        raise CliInputError(str(error)) from error


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        service = AdrReviewService(arguments.workspace.resolve())
        if arguments.command == "start":
            request = _read_model(arguments.input, AdrReviewRequest)
            state = service.start_with_evidence(
                arguments.thread_id,
                arguments.slug,
                AdrStartInput(
                    request=request,
                    evidence_json=_read_exact(arguments.evidence),
                ),
            )
        elif arguments.command == "status":
            state = service.status(arguments.thread_id)
        elif arguments.command == "respond":
            state = service.submit(
                arguments.thread_id,
                arguments.event_id,
                _read_model(arguments.input, ReviewResponse),
            )
        elif arguments.command == "rebut":
            rebuttal = _read_model(arguments.input, AdrRebuttal)
            state = service.submit_adr_rebuttal(
                arguments.thread_id,
                arguments.event_id,
                AdrRebuttalInput(
                    rebuttal=rebuttal,
                    evidence_json=(
                        _read_exact(arguments.evidence)
                        if arguments.evidence is not None
                        else None
                    ),
                ),
            )
        else:
            state = service.submit(
                arguments.thread_id,
                arguments.event_id,
                _read_model(arguments.input, EscalationSummary),
            )
        _emit(_result(arguments.thread_id, state))
        return 0
    except ReviewNotFound as error:
        _emit(
            {"ok": False, "error": str(error), "error_type": type(error).__name__},
            stream=sys.stderr,
        )
        return 3
    except (PersistenceConflict, InvalidTransition, ArtifactConflict) as error:
        _emit(
            {"ok": False, "error": str(error), "error_type": type(error).__name__},
            stream=sys.stderr,
        )
        return 4
    except (
        CliInputError,
        ValidationError,
        ValueError,
    ) as error:
        _emit(
            {"ok": False, "error": str(error), "error_type": type(error).__name__},
            stream=sys.stderr,
        )
        return 2
    except Exception as error:  # noqa: BLE001 - CLI boundary must emit JSON.
        _emit(
            {"ok": False, "error": str(error), "error_type": type(error).__name__},
            stream=sys.stderr,
        )
        return 5


def run() -> None:
    raise SystemExit(main())
