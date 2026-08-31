import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from rubber_ducky.code.cli import main
from rubber_ducky.core.service import ReviewService


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_cli_start_and_status_emit_stable_json(
    tmp_path: Path,
    capsys,
) -> None:
    request_path = tmp_path / "request.json"
    write_json(
        request_path,
        {
            "task_id": "AR-7",
            "title": "Expose CLI",
            "proposed_solution": "Use stable JSON commands.",
            "relevant_diff": "+cli = ready",
        },
    )

    start_code = main(
        [
            "--workspace",
            str(tmp_path),
            "start",
            "review-1",
            "cli",
            "--input",
            str(request_path),
        ]
    )
    started = json.loads(capsys.readouterr().out)
    status_code = main(
        [
            "--workspace",
            str(tmp_path),
            "status",
            "review-1",
        ]
    )
    status = json.loads(capsys.readouterr().out)

    assert start_code == 0
    assert status_code == 0
    assert started["thread_id"] == "review-1"
    assert started["state"]["status"] == "awaiting_review_response"
    assert status == started


def test_cli_respond_is_idempotent_and_audited(
    tmp_path: Path,
    capsys,
) -> None:
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    write_json(
        request_path,
        {
            "task_id": "AR-7",
            "title": "Expose CLI",
            "proposed_solution": "Use stable JSON commands.",
            "relevant_diff": "+cli = ready",
        },
    )
    write_json(
        response_path,
        {
            "round": 1,
            "position": "AGREE",
            "verdict": "APPROVE",
        },
    )
    main(
        [
            "--workspace",
            str(tmp_path),
            "start",
            "review-1",
            "cli",
            "--input",
            str(request_path),
        ]
    )
    capsys.readouterr()

    first_code = main(
        [
            "--workspace",
            str(tmp_path),
            "respond",
            "review-1",
            "event-1",
            "--input",
            str(response_path),
        ]
    )
    first = json.loads(capsys.readouterr().out)
    duplicate_code = main(
        [
            "--workspace",
            str(tmp_path),
            "respond",
            "review-1",
            "event-1",
            "--input",
            str(response_path),
        ]
    )
    duplicate = json.loads(capsys.readouterr().out)

    assert first_code == duplicate_code == 0
    assert first == duplicate
    assert first["state"]["status"] == "approved"
    log = (tmp_path / "rubber_ducky" / "AR-7-cli.md").read_text(encoding="utf-8")
    assert log.count('event id="event-1"') == 1


def test_cli_rebut_advances_review_to_next_round(
    tmp_path: Path,
    capsys,
) -> None:
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    rebuttal_path = tmp_path / "rebuttal.json"
    write_json(
        request_path,
        {
            "task_id": "AR-7",
            "title": "Expose CLI",
            "proposed_solution": "Use stable JSON commands.",
            "relevant_diff": "+cli = ready",
        },
    )
    write_json(
        response_path,
        {
            "round": 1,
            "position": "PARTIAL",
            "blocking_concerns": [
                {
                    "id": "B1",
                    "kind": "blocking",
                    "text": "Still blocked.",
                }
            ],
            "verdict": "REVISE",
        },
    )
    write_json(
        rebuttal_path,
        {
            "round": 1,
            "position": "DISAGREE",
            "blocking_responses": [
                {
                    "concern_id": "B1",
                    "disposition": "DISPUTE",
                    "reason": "The blocker does not apply.",
                }
            ],
            "revised_diff": "Unchanged — see Review Request.",
            "requesting": "RE-REVIEW",
        },
    )
    main(
        [
            "--workspace",
            str(tmp_path),
            "start",
            "review-1",
            "cli",
            "--input",
            str(request_path),
        ]
    )
    capsys.readouterr()
    main(
        [
            "--workspace",
            str(tmp_path),
            "respond",
            "review-1",
            "event-1",
            "--input",
            str(response_path),
        ]
    )
    capsys.readouterr()

    code = main(
        [
            "--workspace",
            str(tmp_path),
            "rebut",
            "review-1",
            "event-2",
            "--input",
            str(rebuttal_path),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["state"]["status"] == "awaiting_review_response"
    assert output["expected_event"] == "review_response"


def test_cli_resume_finalizes_escalation(
    tmp_path: Path,
    capsys,
) -> None:
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    summary_path = tmp_path / "summary.json"
    write_json(
        request_path,
        {
            "task_id": "AR-7",
            "title": "Expose CLI",
            "proposed_solution": "Use stable JSON commands.",
            "relevant_diff": "+cli = ready",
        },
    )
    write_json(
        response_path,
        {
            "round": 1,
            "position": "DISAGREE",
            "blocking_concerns": [
                {
                    "id": "B1",
                    "kind": "blocking",
                    "text": "Deadlocked.",
                }
            ],
            "verdict": "ESCALATE",
        },
    )
    write_json(
        summary_path,
        {
            "unresolved_blocking_concerns": [
                {
                    "concern_id": "B1",
                    "status": "Still disputed.",
                }
            ],
            "worker_position": "The implementation is safe.",
            "reviewer_position": "The blocker remains.",
            "decision_needed": "Should implementation continue?",
        },
    )
    main(
        [
            "--workspace",
            str(tmp_path),
            "start",
            "review-1",
            "cli",
            "--input",
            str(request_path),
        ]
    )
    capsys.readouterr()
    main(
        [
            "--workspace",
            str(tmp_path),
            "respond",
            "review-1",
            "event-1",
            "--input",
            str(response_path),
        ]
    )
    capsys.readouterr()

    code = main(
        [
            "--workspace",
            str(tmp_path),
            "resume",
            "review-1",
            "event-2",
            "--input",
            str(summary_path),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["state"]["status"] == "escalated"
    assert output["expected_event"] is None


def test_cli_returns_json_error_codes(
    tmp_path: Path,
    capsys,
) -> None:
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{", encoding="utf-8")

    input_code = main(
        [
            "--workspace",
            str(tmp_path),
            "start",
            "review-1",
            "cli",
            "--input",
            str(invalid_path),
        ]
    )
    input_error = json.loads(capsys.readouterr().err)
    missing_code = main(
        [
            "--workspace",
            str(tmp_path),
            "status",
            "missing",
        ]
    )
    missing_error = json.loads(capsys.readouterr().err)

    assert input_code == 2
    assert input_error["ok"] is False
    assert missing_code == 3
    assert missing_error["ok"] is False


def test_cli_invalid_transition_returns_conflict_exit_code(
    tmp_path: Path,
    capsys,
) -> None:
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    write_json(
        request_path,
        {
            "task_id": "AR-7",
            "title": "Expose CLI",
            "proposed_solution": "Use stable JSON commands.",
            "relevant_diff": "+cli = ready",
        },
    )
    write_json(
        response_path,
        {
            "round": 2,
            "position": "AGREE",
            "verdict": "APPROVE",
        },
    )
    main(
        [
            "--workspace",
            str(tmp_path),
            "start",
            "review-1",
            "cli",
            "--input",
            str(request_path),
        ]
    )
    capsys.readouterr()

    code = main(
        [
            "--workspace",
            str(tmp_path),
            "respond",
            "review-1",
            "event-1",
            "--input",
            str(response_path),
        ]
    )
    error = json.loads(capsys.readouterr().err)

    assert code == 4
    assert error["ok"] is False
    assert "expected review response round 1" in error["error"]


def test_cli_reads_json_from_stdin(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "task_id": "AR-7",
                    "title": "Expose CLI",
                    "proposed_solution": "Read JSON from stdin.",
                    "relevant_diff": "+stdin = True",
                }
            )
        ),
    )

    code = main(
        [
            "--workspace",
            str(tmp_path),
            "start",
            "review-1",
            "cli",
            "--input",
            "-",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["state"]["status"] == "awaiting_review_response"


def test_cli_unexpected_failure_is_json_with_exit_five(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_status(_service: ReviewService, _thread_id: str) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(ReviewService, "status", fail_status)

    code = main(
        [
            "--workspace",
            str(tmp_path),
            "status",
            "review-1",
        ]
    )
    error = json.loads(capsys.readouterr().err)

    assert code == 5
    assert error == {
        "ok": False,
        "error": "disk unavailable",
        "error_type": "OSError",
    }


def test_cli_review_passes_explicit_model_configuration(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_path = tmp_path / "request.json"
    write_json(
        request_path,
        {
            "task_id": "AR-7",
            "title": "Expose CLI",
            "proposed_solution": "Configure reviewer model.",
            "relevant_diff": "+reviewer = configured",
        },
    )
    main(
        [
            "--workspace",
            str(tmp_path),
            "start",
            "review-1",
            "cli",
            "--input",
            str(request_path),
        ]
    )
    capsys.readouterr()
    captured = {}

    def fake_generate(
        service: ReviewService,
        thread_id: str,
        event_id: str,
        config,
    ):
        captured.update(
            {
                "thread_id": thread_id,
                "event_id": event_id,
                "config": config,
            }
        )
        return service.status(thread_id)

    monkeypatch.setattr(ReviewService, "generate_review", fake_generate)

    code = main(
        [
            "--workspace",
            str(tmp_path),
            "review",
            "review-1",
            "event-1",
            "--provider",
            "openai",
            "--model",
            "gpt-configured",
            "--api-key-env",
            "CUSTOM_OPENAI_KEY",
            "--option",
            "temperature=0",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert captured["thread_id"] == "review-1"
    assert captured["event_id"] == "event-1"
    assert captured["config"].provider == "openai"
    assert captured["config"].model == "gpt-configured"
    assert captured["config"].options == {"temperature": 0}
    assert output["state"]["status"] == "awaiting_review_response"
