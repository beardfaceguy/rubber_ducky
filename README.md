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
agent-review --workspace /path/to/project review THREAD EVENT --provider openai --model MODEL
agent-review --workspace /path/to/project respond THREAD EVENT --input response.json
agent-review --workspace /path/to/project rebut THREAD EVENT --input rebuttal.json
agent-review --workspace /path/to/project resume THREAD EVENT --input summary.json
```

Reviewer selection has no model default. Precedence is explicit CLI/MCP values,
then `AGENT_REVIEW_REVIEWER_PROVIDER` and `AGENT_REVIEW_REVIEWER_MODEL`, then
the global config. When `XDG_CONFIG_HOME` is an absolute path, the file is
`$XDG_CONFIG_HOME/agent_review/config.json`; otherwise it is
`~/.config/agent_review/config.json`. Provider and model must be supplied
together by the same precedence source; values are never mixed across sources.

Create it from the bundled example:

```bash
mkdir -p ~/.config/agent_review
cp examples/config.json ~/.config/agent_review/config.json
cp examples/.env.example ~/.config/agent_review/.env
chmod 600 ~/.config/agent_review/.env
```

Then edit `provider`, `YOUR_MODEL_ID`, `api_key_env`, and the corresponding key
value in `.env` before running a review.

```json
{
  "reviewer": {
    "provider": "anthropic",
    "model": "YOUR_MODEL_ID",
    "api_key_env": "LLM_PROVIDER_KEY"
  }
}
```

Install the selected integration with `uv sync --extra openai` or
`uv sync --extra anthropic`. The tool loads credentials from the global `.env`
without modifying the process environment; real process variables override
file values. Configuration and audit metadata contain only environment-variable
names, provider, and model. The `.env` is credential input only:
provider/model/XDG settings placed there do not participate in configuration
selection, and variable interpolation is disabled.

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
`agent_review_generate`, `agent_review_respond`, `agent_review_rebut`, and
`agent_review_resume`.
Tool failures use MCP `ToolError` with a stable `TypeName: message` string, such
as `ReviewNotFound: 'missing'` or `InvalidTransition: ...`.

The bundled agent skill and authoritative protocol are under
`skill/agent-review/`.
