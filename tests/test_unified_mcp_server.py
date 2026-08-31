import asyncio
from pathlib import Path

from rubber_ducky.mcp_server import server

_CODE_REQUEST = {
    "task_id": "AR-9",
    "title": "Unified server",
    "proposed_solution": "Serve both domains from one server.",
    "relevant_diff": "+server = unified",
}
_PLAN_REQUEST = {
    "task_id": "AR-9",
    "title": "Unified server",
    "proposed_solution": "Serve both domains from one server.",
    "plan": {
        "objective": "Serve both toolsets.",
        "steps": [{"id": "P1", "description": "Register both toolsets."}],
        "acceptance_criteria": ["both domains reachable"],
    },
}


def call_tool(name: str, arguments: dict):
    return asyncio.run(server.call_tool(name, arguments))


def test_unified_server_exposes_both_toolsets() -> None:
    tools = asyncio.run(server.list_tools())
    tool_names = {tool.name for tool in tools}

    assert tool_names == {
        "rubber_ducky_code_start",
        "rubber_ducky_code_status",
        "rubber_ducky_code_respond",
        "rubber_ducky_code_generate",
        "rubber_ducky_code_rebut",
        "rubber_ducky_code_resume",
        "rubber_ducky_plan_start",
        "rubber_ducky_plan_status",
        "rubber_ducky_plan_respond",
        "rubber_ducky_plan_generate",
        "rubber_ducky_plan_rebut",
        "rubber_ducky_plan_resume",
    }


def test_unified_server_runs_a_code_and_a_plan_review(tmp_path: Path) -> None:
    code = call_tool(
        "rubber_ducky_code_start",
        {
            "workspace": str(tmp_path / "code"),
            "thread_id": "review-1",
            "slug": "unified",
            "request": _CODE_REQUEST,
        },
    )
    plan = call_tool(
        "rubber_ducky_plan_start",
        {
            "workspace": str(tmp_path / "plan"),
            "thread_id": "plan-1",
            "slug": "unified",
            "request": _PLAN_REQUEST,
        },
    )

    assert code.structured_content["state"]["status"] == "awaiting_review_response"
    assert plan.structured_content["state"]["status"] == "awaiting_review_response"
    assert (
        plan.structured_content["state"]["request"]["plan"]["objective"]
        == "Serve both toolsets."
    )
