"""MCP transport facade for the durable code-review application service."""

from collections.abc import Callable
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, JsonValue

from rubber_ducky.code.models import Rebuttal, ReviewRequest
from rubber_ducky.code.service import CodeReviewService
from rubber_ducky.core.lifecycle import ReviewState, expected_event_type
from rubber_ducky.core.models import EscalationSummary, ReviewResponse
from rubber_ducky.core.reviewer_config import load_reviewer_config


class ReviewToolResult(BaseModel):
    """Structured MCP result shared by all review tools."""

    thread_id: str
    state: ReviewState
    expected_event: str | None


server = MCPServer(
    name="agent-review",
    description="Durable, protocol-validated agent-to-agent code review",
)


def _service(workspace: str) -> CodeReviewService:
    return CodeReviewService(Path(workspace).expanduser().resolve())


def _result(thread_id: str, state: ReviewState) -> ReviewToolResult:
    return ReviewToolResult(
        thread_id=thread_id,
        state=state,
        expected_event=expected_event_type(state.status),
    )


def _generate_review(
    workspace: str,
    thread_id: str,
    event_id: str,
    provider: str | None,
    model: str | None,
    api_key_env: str | None,
    options: dict[str, JsonValue] | None,
) -> ReviewState:
    workspace_path = Path(workspace).expanduser().resolve()
    config = load_reviewer_config(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        options=options,
    )
    return CodeReviewService(workspace_path).generate_review(
        thread_id,
        event_id,
        config,
    )


def _execute(
    thread_id: str,
    operation: Callable[[], ReviewState],
) -> ReviewToolResult:
    try:
        return _result(thread_id, operation())
    except Exception as error:
        raise ToolError(f"{type(error).__name__}: {error}") from error


_IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_IDEMPOTENT_MODEL_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


@server.tool(
    name="agent_review_start",
    description="Start or idempotently recover a durable code review.",
    annotations=_IDEMPOTENT_WRITE,
    structured_output=True,
)
def start_review(
    workspace: str,
    thread_id: str,
    slug: str,
    request: ReviewRequest,
) -> ReviewToolResult:
    return _execute(
        thread_id,
        lambda: _service(workspace).start(thread_id, slug, request),
    )


@server.tool(
    name="agent_review_status",
    description="Load canonical review status and repair a lagging checkpoint.",
    annotations=_IDEMPOTENT_WRITE,
    structured_output=True,
)
def review_status(workspace: str, thread_id: str) -> ReviewToolResult:
    return _execute(
        thread_id,
        lambda: _service(workspace).status(thread_id),
    )


@server.tool(
    name="agent_review_respond",
    description="Journal and apply a reviewer response.",
    annotations=_IDEMPOTENT_WRITE,
    structured_output=True,
)
def submit_review_response(
    workspace: str,
    thread_id: str,
    event_id: str,
    response: ReviewResponse,
) -> ReviewToolResult:
    return _execute(
        thread_id,
        lambda: _service(workspace).submit(thread_id, event_id, response),
    )


@server.tool(
    name="agent_review_generate",
    description="Generate and apply a reviewer response with configured provider/model.",
    annotations=_IDEMPOTENT_MODEL_WRITE,
    structured_output=True,
)
def generate_review_response(
    workspace: str,
    thread_id: str,
    event_id: str,
    provider: str | None = None,
    model: str | None = None,
    api_key_env: str | None = None,
    options: dict[str, JsonValue] | None = None,
) -> ReviewToolResult:
    return _execute(
        thread_id,
        lambda: _generate_review(
            workspace,
            thread_id,
            event_id,
            provider,
            model,
            api_key_env,
            options,
        ),
    )


@server.tool(
    name="agent_review_rebut",
    description="Journal and apply a worker rebuttal.",
    annotations=_IDEMPOTENT_WRITE,
    structured_output=True,
)
def submit_rebuttal(
    workspace: str,
    thread_id: str,
    event_id: str,
    rebuttal: Rebuttal,
) -> ReviewToolResult:
    return _execute(
        thread_id,
        lambda: _service(workspace).submit(thread_id, event_id, rebuttal),
    )


@server.tool(
    name="agent_review_resume",
    description="Journal an escalation summary and complete human escalation.",
    annotations=_IDEMPOTENT_WRITE,
    structured_output=True,
)
def resume_escalation(
    workspace: str,
    thread_id: str,
    event_id: str,
    summary: EscalationSummary,
) -> ReviewToolResult:
    return _execute(
        thread_id,
        lambda: _service(workspace).submit(thread_id, event_id, summary),
    )


def run() -> None:
    server.run(transport="stdio")
