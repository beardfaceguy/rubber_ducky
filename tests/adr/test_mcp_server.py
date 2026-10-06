import asyncio
from hashlib import sha256
from pathlib import Path

from jsonschema import Draft202012Validator

from rubber_ducky.adr.mcp_server import server
from tests.adr.support import adr_markdown, evidence_json as make_evidence_json


def call_tool(name: str, arguments: dict):
    return asyncio.run(server.call_tool(name, arguments))


def test_adr_mcp_exposes_five_tools_without_generate(tmp_path: Path) -> None:
    markdown = adr_markdown()
    evidence_json = make_evidence_json(markdown)
    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {
        "rubber_ducky_adr_start",
        "rubber_ducky_adr_status",
        "rubber_ducky_adr_respond",
        "rubber_ducky_adr_rebut",
        "rubber_ducky_adr_resume",
    }
    rebut_tool = next(
        tool for tool in tools if tool.name == "rubber_ducky_adr_rebut"
    )
    assert {"rebuttal", "evidence_json"} <= set(
        rebut_tool.input_schema["properties"]
    )
    started = call_tool(
        "rubber_ducky_adr_start",
        {
            "workspace": str(tmp_path),
            "thread_id": "adr-1",
            "slug": "mcp",
            "request": {
                "task_id": "INFRA-108",
                "title": "Add ADR mode",
                "proposed_solution": "Reuse protocol v1.3.",
                "adr_markdown": markdown,
                "adr_sha256": sha256(markdown.encode()).hexdigest(),
                "repository_revision": "abc123",
                "evidence": {
                    "schema_version": "1.0",
                    "relative_path": (
                        "INFRA-108-mcp/artifacts/"
                        "round-1-request-evidence.json"
                    ),
                    "sha256": sha256(evidence_json.encode()).hexdigest(),
                },
            },
            "evidence_json": evidence_json,
        },
    )

    assert started.is_error is False
    assert started.structured_content["state"]["status"] == (
        "awaiting_review_response"
    )
    start_tool = next(
        tool for tool in tools if tool.name == "rubber_ducky_adr_start"
    )
    assert list(
        Draft202012Validator(start_tool.output_schema).iter_errors(
            started.structured_content
        )
    ) == []
