import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from rubber_ducky.plan.mcp_server import server

_PLAN_REQUEST = {
    "task_id": "AR-8",
    "title": "Add plan MCP facade",
    "proposed_solution": "Expose the plan review service.",
    "plan": {
        "objective": "Ship durable plan review.",
        "steps": [{"id": "P1", "description": "Persist plan state."}],
        "acceptance_criteria": ["tests/plan green"],
    },
}


def call_tool(name: str, arguments: dict):
    return asyncio.run(server.call_tool(name, arguments))


def test_plan_mcp_tools_expose_validated_service_contracts(tmp_path: Path) -> None:
    tools = asyncio.run(server.list_tools())
    tool_names = {tool.name for tool in tools}

    assert tool_names == {
        "plan_review_start",
        "plan_review_status",
        "plan_review_respond",
        "plan_review_generate",
        "plan_review_rebut",
        "plan_review_resume",
    }
    start_tool = next(tool for tool in tools if tool.name == "plan_review_start")
    assert "request" in start_tool.input_schema["properties"]

    started = call_tool(
        "plan_review_start",
        {
            "workspace": str(tmp_path),
            "thread_id": "plan-1",
            "slug": "mcp",
            "request": _PLAN_REQUEST,
        },
    )

    assert started.is_error is False
    assert started.structured_content["thread_id"] == "plan-1"
    assert started.structured_content["state"]["status"] == "awaiting_review_response"
    assert (
        started.structured_content["state"]["request"]["plan"]["objective"]
        == "Ship durable plan review."
    )


def test_plan_mcp_status_reports_missing_thread(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="ReviewNotFound"):
        call_tool(
            "plan_review_status",
            {"workspace": str(tmp_path), "thread_id": "missing"},
        )
