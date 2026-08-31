import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from rubber_ducky.code.mcp_server import server
from rubber_ducky.core.service import ReviewService


def test_mcp_tools_expose_validated_service_contracts(tmp_path: Path) -> None:
    tools = asyncio.run(server.list_tools())
    tool_names = {tool.name for tool in tools}

    assert tool_names == {
        "rubber_ducky_code_start",
        "rubber_ducky_code_status",
        "rubber_ducky_code_respond",
        "rubber_ducky_code_generate",
        "rubber_ducky_code_rebut",
        "rubber_ducky_code_resume",
    }
    assert all(tool.annotations.idempotent_hint is True for tool in tools)
    assert all(tool.annotations.read_only_hint is False for tool in tools)
    start_tool = next(tool for tool in tools if tool.name == "rubber_ducky_code_start")
    generate_tool = next(
        tool for tool in tools if tool.name == "rubber_ducky_code_generate"
    )
    assert "request" in start_tool.input_schema["properties"]
    assert generate_tool.annotations.open_world_hint is True

    started = asyncio.run(
        server.call_tool(
            "rubber_ducky_code_start",
            {
                "workspace": str(tmp_path),
                "thread_id": "review-1",
                "slug": "mcp",
                "request": {
                    "task_id": "AR-8",
                    "title": "Add MCP facade",
                    "proposed_solution": "Expose the application service.",
                    "relevant_diff": "+mcp = ready",
                },
            },
        )
    )

    assert started.is_error is False
    assert started.structured_content["thread_id"] == "review-1"
    assert started.structured_content["state"]["status"] == "awaiting_review_response"


def test_mcp_generate_uses_explicit_reviewer_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = str(tmp_path)
    call_tool(
        "rubber_ducky_code_start",
        {
            "workspace": workspace,
            "thread_id": "review-1",
            "slug": "mcp",
            "request": {
                "task_id": "AR-8",
                "title": "Add MCP facade",
                "proposed_solution": "Configure reviewer model.",
                "relevant_diff": "+reviewer = configured",
            },
        },
    )
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

    generated = call_tool(
        "rubber_ducky_code_generate",
        {
            "workspace": workspace,
            "thread_id": "review-1",
            "event_id": "event-1",
            "provider": "anthropic",
            "model": "claude-configured",
            "api_key_env": "CUSTOM_ANTHROPIC_KEY",
            "options": {"temperature": 0},
        },
    )

    assert generated.structured_content["state"]["status"] == "awaiting_review_response"
    assert captured["config"].provider == "anthropic"
    assert captured["config"].model == "claude-configured"
    assert captured["config"].options == {"temperature": 0}


def call_tool(name: str, arguments: dict):
    return asyncio.run(server.call_tool(name, arguments))


def test_mcp_response_rebuttal_and_status_tools(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    call_tool(
        "rubber_ducky_code_start",
        {
            "workspace": workspace,
            "thread_id": "review-1",
            "slug": "mcp",
            "request": {
                "task_id": "AR-8",
                "title": "Add MCP facade",
                "proposed_solution": "Expose the application service.",
                "relevant_diff": "+mcp = ready",
            },
        },
    )
    revision = call_tool(
        "rubber_ducky_code_respond",
        {
            "workspace": workspace,
            "thread_id": "review-1",
            "event_id": "event-1",
            "response": {
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
        },
    )
    rebuttal = call_tool(
        "rubber_ducky_code_rebut",
        {
            "workspace": workspace,
            "thread_id": "review-1",
            "event_id": "event-2",
            "rebuttal": {
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
        },
    )
    status = call_tool(
        "rubber_ducky_code_status",
        {
            "workspace": workspace,
            "thread_id": "review-1",
        },
    )

    assert revision.structured_content["expected_event"] == "rebuttal"
    assert rebuttal.structured_content["expected_event"] == "review_response"
    assert status.structured_content == rebuttal.structured_content


def test_mcp_resume_and_error_results(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    call_tool(
        "rubber_ducky_code_start",
        {
            "workspace": workspace,
            "thread_id": "review-1",
            "slug": "mcp",
            "request": {
                "task_id": "AR-8",
                "title": "Add MCP facade",
                "proposed_solution": "Expose the application service.",
                "relevant_diff": "+mcp = ready",
            },
        },
    )
    call_tool(
        "rubber_ducky_code_respond",
        {
            "workspace": workspace,
            "thread_id": "review-1",
            "event_id": "event-1",
            "response": {
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
        },
    )
    resumed = call_tool(
        "rubber_ducky_code_resume",
        {
            "workspace": workspace,
            "thread_id": "review-1",
            "event_id": "event-2",
            "summary": {
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
        },
    )
    assert resumed.structured_content["state"]["status"] == "escalated"
    with pytest.raises(ToolError, match="ReviewNotFound"):
        call_tool(
            "rubber_ducky_code_status",
            {
                "workspace": workspace,
                "thread_id": "missing",
            },
        )
    with pytest.raises(ToolError, match="InvalidTransition"):
        call_tool(
            "rubber_ducky_code_respond",
            {
                "workspace": workspace,
                "thread_id": "review-1",
                "event_id": "event-3",
                "response": {
                    "round": 2,
                    "position": "AGREE",
                    "verdict": "APPROVE",
                },
            },
        )
