# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja #10 — add MCP facade for agent review
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Expose the durable `ReviewService` through five MCP stdio tools. Tool inputs use
the existing Pydantic protocol models, all outputs share a structured result,
and domain/infrastructure failures become MCP `ToolError`s. Tool annotations
declare idempotent, non-destructive local writes. No domain, lifecycle, or
workflow rule is reimplemented in the transport.

### Relevant Code / Diff
New `src/agent_review/mcp_server.py`:

```python
"""MCP transport facade for the durable review application service."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel

from agent_review.lifecycle import ReviewState, expected_event_type
from agent_review.models import (
    EscalationSummary,
    Rebuttal,
    ReviewRequest,
    ReviewResponse,
)
from agent_review.service import ReviewService


class ReviewToolResult(BaseModel):
    """Structured MCP result shared by all review tools."""

    thread_id: str
    state: dict[str, Any]
    expected_event: str | None


server = MCPServer(
    name="agent-review",
    description="Durable, protocol-validated agent-to-agent code review",
)


def _service(workspace: str) -> ReviewService:
    return ReviewService(Path(workspace).expanduser().resolve())


def _result(thread_id: str, state: ReviewState) -> ReviewToolResult:
    return ReviewToolResult(
        thread_id=thread_id,
        state=state.model_dump(mode="json"),
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
```

Complete `tests/test_mcp_server.py`:

```python
import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agent_review.mcp_server import server


def test_mcp_tools_expose_validated_service_contracts(tmp_path: Path) -> None:
    tools = asyncio.run(server.list_tools())
    tool_names = {tool.name for tool in tools}

    assert tool_names == {
        "agent_review_start",
        "agent_review_status",
        "agent_review_respond",
        "agent_review_rebut",
        "agent_review_resume",
    }
    assert all(tool.annotations.idempotent_hint is True for tool in tools)
    assert all(tool.annotations.read_only_hint is False for tool in tools)
    start_tool = next(tool for tool in tools if tool.name == "agent_review_start")
    assert "request" in start_tool.input_schema["properties"]

    started = asyncio.run(
        server.call_tool(
            "agent_review_start",
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


def call_tool(name: str, arguments: dict):
    return asyncio.run(server.call_tool(name, arguments))


def test_mcp_response_rebuttal_and_status_tools(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    call_tool(
        "agent_review_start",
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
        "agent_review_respond",
        {
            "workspace": workspace,
            "thread_id": "review-1",
            "event_id": "event-1",
            "response": {
                "round": 1,
                "position": "PARTIAL",
                "blocking_concerns": [
                    {"id": "B1", "kind": "blocking", "text": "Still blocked."}
                ],
                "verdict": "REVISE",
            },
        },
    )
    rebuttal = call_tool(
        "agent_review_rebut",
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
        "agent_review_status",
        {"workspace": workspace, "thread_id": "review-1"},
    )

    assert revision.structured_content["expected_event"] == "rebuttal"
    assert rebuttal.structured_content["expected_event"] == "review_response"
    assert status.structured_content == rebuttal.structured_content


def test_mcp_resume_and_error_results(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    call_tool(
        "agent_review_start",
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
        "agent_review_respond",
        {
            "workspace": workspace,
            "thread_id": "review-1",
            "event_id": "event-1",
            "response": {
                "round": 1,
                "position": "DISAGREE",
                "blocking_concerns": [
                    {"id": "B1", "kind": "blocking", "text": "Deadlocked."}
                ],
                "verdict": "ESCALATE",
            },
        },
    )
    resumed = call_tool(
        "agent_review_resume",
        {
            "workspace": workspace,
            "thread_id": "review-1",
            "event_id": "event-2",
            "summary": {
                "unresolved_blocking_concerns": [
                    {"concern_id": "B1", "status": "Still disputed."}
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
            "agent_review_status",
            {"workspace": workspace, "thread_id": "missing"},
        )
```

Operator documentation and skill changes:

```diff
 [project.scripts]
 agent-review = "agent_review.cli:run"
+agent-review-mcp = "agent_review.mcp_server:run"

+"mcp>=2.0.0"

+README documents stdio startup, client configuration, and all five tools.
+The packaged and installed skill list the MCP equivalents, require stable
+thread/event IDs across CLI and MCP, and prohibit reviewer CLI/MCP writes.
```

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ LANGGRAPH_STRICT_MSGPACK=true uv run pytest -q
........................................................................ [ 59%]
..................................................                       [100%]
122 passed in 0.92s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl

$ uv run agent-review-mcp </dev/null
exit 0
```

### Known Concerns
1. The stdio server trusts the local process boundary; it has no network
   authentication because no network transport is enabled.
2. MCP callers choose the workspace path, matching CLI behavior and process
   filesystem permissions.
3. Direct in-process `call_tool` raises `ToolError`; MCP wire transport converts
   it to the protocol's tool-error response.

### Specific Questions for Reviewer
1. Are all service operations exposed with correct Pydantic schemas and
   structured results?
2. Do tool annotations and error conversion accurately describe behavior?
3. Does the MCP facade preserve service idempotency and reviewer read-only
   boundaries without duplicating domain logic?

## Review Response — Round 1
**Position:** AGREE

Verification basis: the code in the Review Request matches the working tree exactly (`src/agent_review/mcp_server.py`, `tests/test_mcp_server.py`, `pyproject.toml` script and dependency entries). `mcp` 2.0.0 is locked in `uv.lock` and installed; every imported symbol exists in the locked package, including `ToolAnnotations(readOnlyHint=..., ...)` construction, which is valid because `mcp_types.MCPModel` sets `alias_generator=to_camel, populate_by_name=True`. Per the read-only mandate I did not rerun the suite; the reported 122-pass run is consistent with the fresh build artifacts in `dist/` and I found nothing statically that contradicts it.

On the three specific questions: (1) Yes — all five `ReviewService` operations are exposed, tool inputs are the existing domain Pydantic models, and all outputs share `ReviewToolResult` with `structured_output=True`. (2) Yes — the annotations are accurate, including the non-obvious ones: `readOnlyHint=False` on `agent_review_status` is correct because `status` repairs lagging checkpoints via `_reconcile_graph`; `destructiveHint=False` holds because all writes are additive (the at-least-once audit duplication is additive, not destructive); `idempotentHint=True` is backed by `create_review`'s same-payload recovery (persistence.py lines 130–147) and `append_event_once`'s same-`event_id` no-op (lines 190–195). `ToolError` is the SDK's documented tool-failure channel, so the in-process raise / wire `is_error` split in Known Concern 3 is correct. (3) Yes — every tool body is a one-line delegation to `ReviewService`; no round, ID, or transition rule is duplicated in the transport, and the reviewer write prohibition is stated in the packaged skill, which is byte-identical to the installed copy.

### Blocking Concerns
None.

### Non-Blocking Suggestions
1. **S1** — `ReviewToolResult.state` is declared `dict[str, Any]` (mcp_server.py line 27), so the published output schema presents `state` as an opaque object even though every value is a serialized `ReviewState`. Declaring the field as `ReviewState` would give MCP clients a full, introspectable schema at no runtime cost.
2. **S2** — `_execute` flattens every failure into `ToolError(f"{type(error).__name__}: {error}")`. The CLI gives callers a machine-readable `error_type` plus exit codes 3/4/2/5; MCP clients must parse the message prefix to distinguish `ReviewNotFound` from `PersistenceConflict` or `InvalidTransition`. That prefix is load-bearing (test at tests/test_mcp_server.py line 178 matches on it) but undocumented — state the `TypeName: message` contract in the README MCP section or the tool descriptions.
3. **S3** — MCP-layer error coverage exercises only `ReviewNotFound`; there is no test that `InvalidTransition` or `PersistenceConflict` surfaces through `_execute` as a `ToolError`. Also the synchronous `call_tool` helper is defined between tests (line 48) and unused by the first test. Both are minor tidiness items, adequately mitigated by service- and CLI-layer coverage.

**Verdict:** APPROVE

## Post-Approval Follow-Up
All suggestions were adopted:

- S1: `ReviewToolResult.state` now exposes the full `ReviewState` schema.
- S2: README documents the stable MCP `TypeName: message` error contract.
- S3: MCP tests now cover `InvalidTransition` as well as `ReviewNotFound`.

Final verification: Ruff passes, all 122 tests pass under strict checkpoint
mode, the package builds, and the stdio entry point starts and exits cleanly on
EOF.
