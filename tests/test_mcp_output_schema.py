import asyncio
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from mcp.server.mcpserver.exceptions import ToolError

from rubber_ducky.code.mcp_server import server as code_server
from rubber_ducky.code.service import CodeReviewService
from rubber_ducky.core.models import Concern, ConcernKind, ReviewResponse
from rubber_ducky.plan.mcp_server import server as plan_server
from rubber_ducky.plan.service import PlanReviewService


_PLAN_REQUEST = {
    "task_id": "SCHEMA-PLAN",
    "title": "Validate plan MCP output",
    "proposed_solution": "Return the canonical plan review state.",
    "plan": {
        "objective": "Keep the MCP schema aligned with plan state.",
        "steps": [{"id": "P1", "description": "Validate structured content."}],
        "acceptance_criteria": ["Every tool result matches its output schema."],
    },
}
_PLAN_REBUTTAL = {
    "round": 1,
    "position": "DISAGREE",
    "blocking_responses": [
        {
            "concern_id": "B1",
            "disposition": "DISPUTE",
            "reason": "The returned state now has a domain-specific schema.",
        }
    ],
    "revised_plan": None,
    "requesting": "RE-REVIEW",
}
_CODE_REQUEST = {
    "task_id": "SCHEMA-CODE",
    "title": "Validate code MCP output",
    "proposed_solution": "Return the canonical code review state.",
    "relevant_diff": "+schema_matches = True",
}
_CODE_REBUTTAL = {
    "round": 1,
    "position": "DISAGREE",
    "blocking_responses": [
        {
            "concern_id": "B1",
            "disposition": "DISPUTE",
            "reason": "The returned state now has a domain-specific schema.",
        }
    ],
    "revised_diff": "Unchanged — see Review Request.",
    "requesting": "RE-REVIEW",
}


def _call_tool(server, name: str, arguments: dict):
    return asyncio.run(server.call_tool(name, arguments))


def _tool(server, name: str):
    return next(tool for tool in asyncio.run(server.list_tools()) if tool.name == name)


def _schema_errors(server, name: str, structured_content: dict) -> list[str]:
    validator = Draft202012Validator(_tool(server, name).output_schema)
    return [
        f"{'/'.join(map(str, error.absolute_path))}: {error.message}"
        for error in validator.iter_errors(structured_content)
    ]


def _definition_with_title(schema: dict, title: str) -> dict:
    return next(
        definition
        for definition in schema["$defs"].values()
        if definition.get("title") == title
    )


@pytest.mark.parametrize(
    (
        "server",
        "service_type",
        "tool_prefix",
        "request_payload",
        "request_field",
        "request_model_title",
        "rebuttal",
        "rebuttal_field",
        "rebuttal_model_title",
    ),
    [
        pytest.param(
            plan_server,
            PlanReviewService,
            "rubber_ducky_plan",
            _PLAN_REQUEST,
            "plan",
            "PlanReviewRequest",
            _PLAN_REBUTTAL,
            "revised_plan",
            "PlanRebuttal",
            id="plan",
        ),
        pytest.param(
            code_server,
            CodeReviewService,
            "rubber_ducky_code",
            _CODE_REQUEST,
            "relevant_diff",
            "ReviewRequest",
            _CODE_REBUTTAL,
            "revised_diff",
            "Rebuttal",
            id="code",
        ),
    ],
)
def test_mutating_tool_results_match_their_advertised_output_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server,
    service_type,
    tool_prefix: str,
    request_payload: dict,
    request_field: str,
    request_model_title: str,
    rebuttal: dict,
    rebuttal_field: str,
    rebuttal_model_title: str,
) -> None:
    workspace = str(tmp_path)
    thread_id = "schema-review"
    slug = "output-schema"
    blocking_response = ReviewResponse(
        round=1,
        position="PARTIAL",
        blocking_concerns=(
            Concern(
                id="B1",
                kind=ConcernKind.BLOCKING,
                text="Verify domain-specific state serialization.",
            ),
        ),
        verdict="REVISE",
    )

    def fake_generate(service, generated_thread_id, event_id, config):
        return service.submit(
            generated_thread_id,
            event_id,
            blocking_response,
            metadata={
                **config.audit_metadata(),
                "validation_attempts": "1",
            },
        )

    monkeypatch.setattr(service_type, "generate_review", fake_generate)

    start_arguments = {
        "workspace": workspace,
        "thread_id": thread_id,
        "slug": slug,
        "request": request_payload,
    }
    generate_arguments = {
        "workspace": workspace,
        "thread_id": thread_id,
        "event_id": "review-1",
        "provider": "openrouter",
        "model": "test/reviewer",
    }
    rebut_arguments = {
        "workspace": workspace,
        "thread_id": thread_id,
        "event_id": "rebuttal-1",
        "rebuttal": rebuttal,
    }

    started = _call_tool(server, f"{tool_prefix}_start", start_arguments)
    generated = _call_tool(server, f"{tool_prefix}_generate", generate_arguments)
    rebutted = _call_tool(server, f"{tool_prefix}_rebut", rebut_arguments)
    status = _call_tool(
        server,
        f"{tool_prefix}_status",
        {"workspace": workspace, "thread_id": thread_id},
    )
    results = [
        (f"{tool_prefix}_start", started),
        (f"{tool_prefix}_status", status),
        (f"{tool_prefix}_generate", generated),
        (f"{tool_prefix}_rebut", rebutted),
    ]

    errors = {
        name: schema_errors
        for name, result in results
        if (
            schema_errors := _schema_errors(
                server,
                name,
                result.structured_content,
            )
        )
    }
    assert errors == {}

    output_schema = _tool(server, f"{tool_prefix}_start").output_schema
    request_schema = _definition_with_title(output_schema, request_model_title)
    rebuttal_schema = _definition_with_title(output_schema, rebuttal_model_title)
    assert request_field in request_schema["properties"]
    assert rebuttal_field in rebuttal_schema["properties"]

    retried_start = _call_tool(server, f"{tool_prefix}_start", start_arguments)
    retried_generate = _call_tool(server, f"{tool_prefix}_generate", generate_arguments)
    retried_rebuttal = _call_tool(server, f"{tool_prefix}_rebut", rebut_arguments)
    assert _schema_errors(
        server,
        f"{tool_prefix}_start",
        retried_start.structured_content,
    ) == []
    assert _schema_errors(
        server,
        f"{tool_prefix}_generate",
        retried_generate.structured_content,
    ) == []
    assert _schema_errors(
        server,
        f"{tool_prefix}_rebut",
        retried_rebuttal.structured_content,
    ) == []

    history = service_type(tmp_path).store.load_history(thread_id)
    assert tuple(event.event_id for event in history.events) == (
        "review-1",
        "rebuttal-1",
    )
    journal = (
        tmp_path
        / "rubber_ducky"
        / f"{request_payload['task_id']}-{slug}.md"
    ).read_text(encoding="utf-8")
    assert journal.count('event id="request"') == 1
    assert journal.count('event id="review-1"') == 1
    assert journal.count('event id="rebuttal-1"') == 1

    conflicting_request = {**request_payload, "title": "Conflicting retry"}
    with pytest.raises(ToolError, match="PersistenceConflict: review"):
        _call_tool(
            server,
            f"{tool_prefix}_start",
            {**start_arguments, "request": conflicting_request},
        )

    conflicting_rebuttal = {
        **rebuttal,
        "blocking_responses": [
            {
                **rebuttal["blocking_responses"][0],
                "reason": "Conflicting retry data.",
            }
        ],
    }
    with pytest.raises(ToolError, match="PersistenceConflict: event ID"):
        _call_tool(
            server,
            f"{tool_prefix}_rebut",
            {**rebut_arguments, "rebuttal": conflicting_rebuttal},
        )
