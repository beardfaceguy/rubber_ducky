# agent-review

Deterministic orchestration for formal agent-to-agent code review. The
application enforces protocol rounds, stable concern IDs, durable state,
human-readable audit logs, and reviewer read-only boundaries.

## Development

```bash
uv sync
uv run pytest
uv run agent-review --help
```

## CLI

The CLI accepts Pydantic-compatible JSON from a file or stdin:

```bash
agent-review --workspace /path/to/project start THREAD SLUG --input request.json
agent-review --workspace /path/to/project status THREAD
agent-review --workspace /path/to/project respond THREAD EVENT --input response.json
agent-review --workspace /path/to/project rebut THREAD EVENT --input rebuttal.json
agent-review --workspace /path/to/project resume THREAD EVENT --input summary.json
```

## MCP

Run the stdio MCP server with:

```bash
agent-review-mcp
```

Example client configuration:

```json
{
  "mcpServers": {
    "agent-review": {
      "command": "agent-review-mcp"
    }
  }
}
```

The server exposes `agent_review_start`, `agent_review_status`,
`agent_review_respond`, `agent_review_rebut`, and `agent_review_resume`.
Tool failures use MCP `ToolError` with a stable `TypeName: message` string, such
as `ReviewNotFound: 'missing'` or `InvalidTransition: ...`.

The bundled agent skill and authoritative protocol are under
`skill/agent-review/`.
