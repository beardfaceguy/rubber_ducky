"""MCP transport facade for durable ADR reviews."""

from collections.abc import Callable
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel

from rubber_ducky.adr.models import (
    AdrRebuttal,
    AdrRebuttalInput,
    AdrReviewRequest,
    AdrReviewState,
    AdrStartInput,
)
from rubber_ducky.adr.service import AdrReviewService
from rubber_ducky.core.lifecycle import ReviewState, expected_event_type
from rubber_ducky.core.models import EscalationSummary, ReviewResponse


class ReviewToolResult(BaseModel):
    """Structured result shared by all ADR review tools."""

    thread_id: str
    state: AdrReviewState
    expected_event: str | None


def _service(workspace: str) -> AdrReviewService:
    return AdrReviewService(Path(workspace).expanduser().resolve())


def _result(thread_id: str, state: ReviewState) -> ReviewToolResult:
    return ReviewToolResult(
        thread_id=thread_id,
        state=state,
        expected_event=expected_event_type(state.status),
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


def start_review(
    workspace: str,
    thread_id: str,
    slug: str,
    request: AdrReviewRequest,
    evidence_json: str,
) -> ReviewToolResult:
    return _execute(
        thread_id,
        lambda: _service(workspace).start_with_evidence(
            thread_id,
            slug,
            AdrStartInput(request=request, evidence_json=evidence_json),
        ),
    )


def review_status(workspace: str, thread_id: str) -> ReviewToolResult:
    return _execute(thread_id, lambda: _service(workspace).status(thread_id))


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


def submit_rebuttal(
    workspace: str,
    thread_id: str,
    event_id: str,
    rebuttal: AdrRebuttal,
    evidence_json: str | None = None,
) -> ReviewToolResult:
    return _execute(
        thread_id,
        lambda: _service(workspace).submit_adr_rebuttal(
            thread_id,
            event_id,
            AdrRebuttalInput(
                rebuttal=rebuttal,
                evidence_json=evidence_json,
            ),
        ),
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


def register_tools(target: MCPServer) -> None:
    """Register the five ADR review tools onto ``target``."""

    target.tool(
        name="rubber_ducky_adr_start",
        description="Start or recover a durable ADR review with exact evidence.",
        annotations=_IDEMPOTENT_WRITE,
        structured_output=True,
    )(start_review)
    target.tool(
        name="rubber_ducky_adr_status",
        description="Load canonical ADR review status and repair its checkpoint.",
        annotations=_IDEMPOTENT_WRITE,
        structured_output=True,
    )(review_status)
    target.tool(
        name="rubber_ducky_adr_respond",
        description="Journal and apply an independent reviewer response.",
        annotations=_IDEMPOTENT_WRITE,
        structured_output=True,
    )(submit_review_response)
    target.tool(
        name="rubber_ducky_adr_rebut",
        description="Journal an author rebuttal and optional revised ADR/evidence.",
        annotations=_IDEMPOTENT_WRITE,
        structured_output=True,
    )(submit_rebuttal)
    target.tool(
        name="rubber_ducky_adr_resume",
        description="Journal an escalation summary and complete human escalation.",
        annotations=_IDEMPOTENT_WRITE,
        structured_output=True,
    )(resume_escalation)


server = MCPServer(
    name="rubber-ducky-adr",
    description="Durable, evidence-bound agent-to-agent ADR review",
)
register_tools(server)


def run() -> None:
    server.run(transport="stdio")
