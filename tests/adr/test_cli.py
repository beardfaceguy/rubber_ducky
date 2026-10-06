import json
from hashlib import sha256
from pathlib import Path

import pytest

from rubber_ducky.adr.cli import main
from tests.adr.support import adr_markdown, evidence_json as make_evidence_json


def test_adr_cli_start_then_status_preserves_evidence_bytes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    markdown = adr_markdown(newline="\r\n")
    evidence_json = make_evidence_json(markdown)
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "task_id": "INFRA-108",
                "title": "Add ADR mode",
                "proposed_solution": "Reuse protocol v1.3.",
                "adr_markdown": markdown,
                "adr_sha256": sha256(markdown.encode()).hexdigest(),
                "repository_revision": "abc123",
                "evidence": {
                    "schema_version": "1.0",
                    "relative_path": (
                        "INFRA-108-adr-cli/artifacts/"
                        "round-1-request-evidence.json"
                    ),
                    "sha256": sha256(evidence_json.encode()).hexdigest(),
                },
            }
        )
    )
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(evidence_json.encode())

    exit_code = main(
        [
            "--workspace",
            str(tmp_path),
            "start",
            "adr-1",
            "adr-cli",
            "--input",
            str(request_path),
            "--evidence",
            str(evidence_path),
        ]
    )
    started = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert started["state"]["status"] == "awaiting_review_response"
    assert main(["--workspace", str(tmp_path), "status", "adr-1"]) == 0


def test_adr_cli_has_no_automatic_review_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["review", "adr-1", "event-1"])
    error = json.loads(capsys.readouterr().err)

    assert exit_code == 2
    assert error["error_type"] == "CliInputError"
