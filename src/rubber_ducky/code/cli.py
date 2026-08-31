"""Stable JSON command-line interface for durable code reviews."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from pydantic import BaseModel, ValidationError

from rubber_ducky.code.models import Rebuttal, ReviewRequest
from rubber_ducky.code.service import CodeReviewService
from rubber_ducky.core.lifecycle import (
    InvalidTransition,
    ReviewState,
    expected_event_type,
)
from rubber_ducky.core.models import EscalationSummary, ReviewResponse
from rubber_ducky.core.persistence import PersistenceConflict, ReviewNotFound
from rubber_ducky.core.reviewer_config import (
    ReviewerConfigurationError,
    load_reviewer_config,
)


class CliInputError(ValueError):
    """Raised for command syntax or input-document errors."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliInputError(message)


def _build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(prog="agent-review")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start")
    start.add_argument("thread_id")
    start.add_argument("slug")
    start.add_argument("--input", required=True)

    status = commands.add_parser("status")
    status.add_argument("thread_id")

    for name in ("respond", "rebut", "resume"):
        event = commands.add_parser(name)
        event.add_argument("thread_id")
        event.add_argument("event_id")
        event.add_argument("--input", required=True)

    review = commands.add_parser("review")
    review.add_argument("thread_id")
    review.add_argument("event_id")
    review.add_argument("--provider")
    review.add_argument("--model")
    review.add_argument("--api-key-env")
    review.add_argument("--option", action="append", default=[])
    return parser


def _read_model(path: str, model: type[BaseModel]) -> BaseModel:
    try:
        text = (
            sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        )
        payload = json.loads(text)
    except (OSError, json.JSONDecodeError) as error:
        raise CliInputError(str(error)) from error
    return model.model_validate(payload)


def _result(thread_id: str, state: ReviewState) -> dict[str, Any]:
    return {
        "ok": True,
        "thread_id": thread_id,
        "state": state.model_dump(mode="json"),
        "expected_event": expected_event_type(state.status),
    }


def _parse_options(values: list[str]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for value in values:
        key, separator, raw_value = value.partition("=")
        if not separator or not key:
            raise CliInputError("--option must use KEY=JSON")
        try:
            options[key] = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise CliInputError(f"invalid JSON for option {key!r}: {error}") from error
    return options


def _emit(payload: dict[str, Any], *, stream: TextIO | None = None) -> None:
    output = sys.stdout if stream is None else stream
    output.write(json.dumps(payload, sort_keys=True) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one CLI command and return a stable process exit code."""

    try:
        arguments = _build_parser().parse_args(argv)
        service = CodeReviewService(arguments.workspace.resolve())
        if arguments.command == "start":
            request = _read_model(arguments.input, ReviewRequest)
            state = service.start(arguments.thread_id, arguments.slug, request)
        elif arguments.command == "status":
            state = service.status(arguments.thread_id)
        elif arguments.command == "review":
            config = load_reviewer_config(
                provider=arguments.provider,
                model=arguments.model,
                api_key_env=arguments.api_key_env,
                options=_parse_options(arguments.option),
            )
            state = service.generate_review(
                arguments.thread_id,
                arguments.event_id,
                config,
            )
        else:
            model = {
                "respond": ReviewResponse,
                "rebut": Rebuttal,
                "resume": EscalationSummary,
            }[arguments.command]
            event = _read_model(arguments.input, model)
            state = service.submit(arguments.thread_id, arguments.event_id, event)
        _emit(_result(arguments.thread_id, state))
        return 0
    except ReviewNotFound as error:
        _emit(
            {"ok": False, "error": str(error), "error_type": type(error).__name__},
            stream=sys.stderr,
        )
        return 3
    except (PersistenceConflict, InvalidTransition) as error:
        _emit(
            {"ok": False, "error": str(error), "error_type": type(error).__name__},
            stream=sys.stderr,
        )
        return 4
    except (
        CliInputError,
        ReviewerConfigurationError,
        ValidationError,
        ValueError,
    ) as error:
        _emit(
            {"ok": False, "error": str(error), "error_type": type(error).__name__},
            stream=sys.stderr,
        )
        return 2
    except Exception as error:  # noqa: BLE001 - CLI boundary must always emit JSON.
        _emit(
            {"ok": False, "error": str(error), "error_type": type(error).__name__},
            stream=sys.stderr,
        )
        return 5


def run() -> None:
    raise SystemExit(main())
