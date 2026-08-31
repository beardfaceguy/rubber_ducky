# agent-review

`agent-review` gives a coding agent a second AI reviewer and keeps their
conversation organized. The reviewer examines the actual code or diff, while
the calling agent remains responsible for deciding whether each concern is
correct and for making any resulting changes. Review history is saved so a
crash or restarted agent does not lose the discussion.

## How a review works

1. The calling agent sends the task and actual code or diff for review.
2. The reviewer either approves it or identifies blocking concerns.
3. For every blocker, the calling agent must analyze the concern and respond
   with a reasoned `ACCEPT`, `DISPUTE`, or `CLARIFY`:
   - `ACCEPT` includes the revised code or diff.
   - `DISPUTE` explains why the concern does not apply, with evidence when
     possible.
   - `CLARIFY` explains what the reviewer misunderstood.
4. The reviewer checks that response and any revised code. A concern is not
   resolved merely because the calling agent accepted it; the reviewer must
   verify the change.
5. The exchange continues until the reviewer returns `APPROVE`, or until the
   third and final review round. If blockers remain after round three, the
   agents record their final positions and ask a human to choose the best
   course of action.

There are at most three reviewer responses and at most one calling-agent
rebuttal after each response—not three sets of three rebuttals. The MCP server
enforces the order and required response fields. The bundled agent skill tells
the calling agent to evaluate concerns honestly rather than automatically
agreeing with the reviewer.

## Installation

Clone the repository and install the CLI and MCP server as a `uv` tool:

```bash
git clone https://github.com/beardfaceguy/agent_review.git
cd agent_review
uv tool install --editable '.[anthropic,openai,openrouter]'
command -v agent-review-mcp
```

The final command prints the absolute executable path used in the MCP client
configuration below. Install only the provider extras you need if you do not
want both.

Install the bundled calling-agent instructions:

```bash
mkdir -p ~/.agents/skills/agent-review
cp -R skill/agent-review/. ~/.agents/skills/agent-review/
```

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

Install the selected integration with `uv sync --extra openai`,
`uv sync --extra anthropic`, or `uv sync --extra openrouter`. The tool loads
credentials from the global `.env` without modifying the process environment;
real process variables override file values. Configuration and audit metadata
contain only environment-variable names, provider, and model. The `.env` is
credential input only: provider/model/XDG settings placed there do not
participate in configuration selection, and variable interpolation is
disabled.

### OpenRouter reviewer

OpenRouter uses LangChain's native `langchain-openrouter` integration. Set
`provider` to `openrouter` and use the complete OpenRouter model slug:

```json
{
  "reviewer": {
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4.6",
    "api_key_env": "LLM_PROVIDER_KEY",
    "options": {
      "default_headers": {
        "HTTP-Referer": "https://example.com",
        "X-OpenRouter-Title": "Agent Review"
      }
    }
  }
}
```

The attribution headers are optional and must identify your own application.
`LLM_PROVIDER_KEY` remains the generic default credential variable. To use the
conventional OpenRouter name instead, set `api_key_env` to
`OPENROUTER_API_KEY`. Do not put either credential value in `options`.

Reviewer output that fails protocol schema validation receives one corrective
retry. A successful retry records its attempt count and redacted validation
diagnostics in the durable audit; a second invalid response fails closed
without persisting an event.

## MCP

The server exposes `agent_review_start`, `agent_review_status`,
`agent_review_generate`, `agent_review_respond`, `agent_review_rebut`, and
`agent_review_resume`. It uses stdio; MCP clients start it automatically.

Use the absolute path printed by `command -v agent-review-mcp`. The examples
below use `/absolute/path/to/agent-review-mcp`. Merge each entry into an
existing configuration rather than replacing unrelated servers.

### Cursor

Add this to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "agent-review": {
      "command": "/absolute/path/to/agent-review-mcp",
      "args": []
    }
  }
}
```

### Claude Code

Register it for all projects:

```bash
claude mcp add --scope user agent-review -- /absolute/path/to/agent-review-mcp
claude mcp get agent-review
```

### Claude Desktop

On Linux, add this to
`~/.config/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agent-review": {
      "command": "/absolute/path/to/agent-review-mcp",
      "args": []
    }
  }
}
```

### OpenAI Codex

Register it with:

```bash
codex mcp add agent-review -- /absolute/path/to/agent-review-mcp
codex mcp get agent-review
```

The corresponding `~/.codex/config.toml` entry is:

```toml
[mcp_servers.agent-review]
command = "/absolute/path/to/agent-review-mcp"
```

### Zed

Merge this into `~/.config/zed/settings.json`:

```json
{
  "context_servers": {
    "agent-review": {
      "enabled": true,
      "command": "/absolute/path/to/agent-review-mcp",
      "args": [],
      "timeout": 600
    }
  }
}
```

### Daimonos

Add this to the `mcpServers` object in
`~/.config/daimonos/mcp_servers.json`:

```json
{
  "mcpServers": {
    "agent-review": {
      "command": "/absolute/path/to/agent-review-mcp",
      "args": []
    }
  }
}
```

Restart clients that were already running, then verify that the six
`agent_review_*` tools are available. A quick end-to-end check should start a
review in a disposable workspace, generate a reviewer response, and query its
status.

Tool failures use MCP `ToolError` with a stable `TypeName: message` string, such
as `ReviewNotFound: 'missing'` or `InvalidTransition: ...`.

The bundled agent skill and authoritative protocol are under
`skill/agent-review/`.
