import json
from pathlib import Path

import pytest

from rubber_ducky.plan.cli import main

_PLAN_REQUEST = {
    "task_id": "AR-8",
    "title": "Add plan CLI",
    "proposed_solution": "Bind the shared CLI to plan payloads.",
    "plan": {
        "objective": "Ship durable plan review.",
        "steps": [{"id": "P1", "description": "Persist plan state."}],
        "acceptance_criteria": ["tests/plan green"],
    },
}


def _write(path: Path, payload: dict) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_plan_cli_start_then_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    request_path = _write(tmp_path / "request.json", _PLAN_REQUEST)

    exit_code = main(
        [
            "--workspace",
            str(tmp_path),
            "start",
            "plan-1",
            "add-rubber-ducky-plan",
            "--input",
            request_path,
        ]
    )
    started = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert started["ok"] is True
    assert started["state"]["status"] == "awaiting_review_response"
    assert (
        started["state"]["request"]["plan"]["objective"] == "Ship durable plan review."
    )

    status_code = main(["--workspace", str(tmp_path), "status", "plan-1"])
    status = json.loads(capsys.readouterr().out)

    assert status_code == 0
    assert status["state"]["status"] == "awaiting_review_response"


def test_plan_cli_reports_missing_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--workspace", str(tmp_path), "status", "missing"])
    error = json.loads(capsys.readouterr().err)

    assert exit_code == 3
    assert error["ok"] is False
    assert error["error_type"] == "ReviewNotFound"
