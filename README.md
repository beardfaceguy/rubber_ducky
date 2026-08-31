# rubber-ducky

`rubber-ducky` gives a coding agent a second AI reviewer and keeps their
conversation organized. The reviewer examines the actual artifact under review,
while the calling agent remains responsible for deciding whether each concern
is correct and for making any resulting changes. Review history is saved so a
crash or restarted agent does not lose the discussion.

It reviews two kinds of artifact through one shared protocol:

- **Code review** — the reviewed artifact is a real diff. CLI `agent-review`,
  MCP tools `agent_review_*`, skill `rubber_ducky_code`.
- **Plan review** — the reviewed artifact is a structured plan document. CLI
  `plan-review`, MCP tools `plan_review_*`, skill `rubber_ducky_plan`.

The `rubber_ducky` router skill picks the domain from the supplied artifact.
Both domains share the same rounds, verdicts, escalation, durable audit log,
and reviewer configuration; only the payload differs.

## How a review works

1. The calling agent sends the task and the actual artifact (a code diff or a
   plan document) for review.
2. The reviewer either approves it or identifies blocking concerns.
3. For every blocker, the calling agent must analyze the concern and respond
   with a reasoned `ACCEPT`, `DISPUTE`, or `CLARIFY`:
   - `ACCEPT` includes the revised artifact (revised diff or revised plan).
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
command -v rubber-ducky-mcp
```

This installs five executables: the `agent-review` and `plan-review` CLIs, and
three MCP servers — `rubber-ducky-mcp` (both toolsets), `agent-review-mcp`
(code only), and `plan-review-mcp` (plan only). The final command prints the
absolute path used in the MCP client configuration below. Install only the
provider extras you need.

Install the bundled calling-agent skills (router plus both domains):

```bash
mkdir -p ~/.agents/skills
cp -R skill/rubber_ducky ~/.agents/skills/rubber_ducky
cp -R skill/rubber_ducky_code ~/.agents/skills/rubber_ducky_code
cp -R skill/rubber_ducky_plan ~/.agents/skills/rubber_ducky_plan
cp -R skill/references ~/.agents/skills/references
```

The domain skills link the shared protocol at `../references/review-protocol.md`,
so keep the `references/` directory alongside them.

## Development

```bash
uv sync
uv run pytest
uv run agent-review --help
```

## CLI

Both CLIs accept Pydantic-compatible JSON from a file or stdin and share the
same subcommands and exit codes. `agent-review` reviews a diff; `plan-review`
reviews a plan document. Substitute `plan-review` for `agent-review` below to
run a plan review.

```bash
agent-review --workspace /path/to/project start THREAD SLUG --input request.json
agent-review --workspace /path/to/project status THREAD
agent-review --workspace /path/to/project review THREAD EVENT --provider openai --model MODEL
agent-review --workspace /path/to/project respond THREAD EVENT --input response.json
agent-review --workspace /path/to/project rebut THREAD EVENT --input rebuttal.json
agent-review --workspace /path/to/project resume THREAD EVENT --input summary.json
```

For a code review the `start` request carries a `relevant_diff` string. For a
plan review it carries a structured `plan` instead:

```json
{
  "task_id": "AR-8",
  "title": "Add plan review domain",
  "proposed_solution": "Mirror the code domain for plans.",
  "plan": {
    "objective": "Ship durable plan review.",
    "steps": [{ "id": "P1", "description": "Define the plan schema." }],
    "acceptance_criteria": ["tests/plan green"]
  }
}
```

A `plan-review rebut` input carries `revised_plan` (a full plan document, or
`null` when nothing changed) in place of the code domain's `revised_diff`.

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

The unified server `rubber-ducky-mcp` exposes both toolsets on one process: the
code tools `agent_review_start`, `agent_review_status`, `agent_review_generate`,
`agent_review_respond`, `agent_review_rebut`, `agent_review_resume`, and the
plan tools `plan_review_start`, `plan_review_status`, `plan_review_generate`,
`plan_review_respond`, `plan_review_rebut`, `plan_review_resume`. It uses stdio;
MCP clients start it automatically. The standalone `agent-review-mcp` and
`plan-review-mcp` servers expose only their own toolset if you prefer to
register a single domain.

Use the absolute path printed by `command -v rubber-ducky-mcp`. The examples
below use `/absolute/path/to/rubber-ducky-mcp`. Merge each entry into an
existing configuration rather than replacing unrelated servers.

### Cursor

Add this to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "rubber-ducky": {
      "command": "/absolute/path/to/rubber-ducky-mcp",
      "args": []
    }
  }
}
```

### Claude Code

Register it for all projects:

```bash
claude mcp add --scope user rubber-ducky -- /absolute/path/to/rubber-ducky-mcp
claude mcp get rubber-ducky
```

### Claude Desktop

On Linux, add this to
`~/.config/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "rubber-ducky": {
      "command": "/absolute/path/to/rubber-ducky-mcp",
      "args": []
    }
  }
}
```

### OpenAI Codex

Register it with:

```bash
codex mcp add rubber-ducky -- /absolute/path/to/rubber-ducky-mcp
codex mcp get rubber-ducky
```

The corresponding `~/.codex/config.toml` entry is:

```toml
[mcp_servers.rubber-ducky]
command = "/absolute/path/to/rubber-ducky-mcp"
```

### Zed

Merge this into `~/.config/zed/settings.json`:

```json
{
  "context_servers": {
    "rubber-ducky": {
      "enabled": true,
      "command": "/absolute/path/to/rubber-ducky-mcp",
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
    "rubber-ducky": {
      "command": "/absolute/path/to/rubber-ducky-mcp",
      "args": []
    }
  }
}
```

Restart clients that were already running, then verify that the twelve
`agent_review_*` and `plan_review_*` tools are available. A quick end-to-end
check should start a review in a disposable workspace, generate a reviewer
response, and query its status.

Tool failures use MCP `ToolError` with a stable `TypeName: message` string, such
as `ReviewNotFound: 'missing'` or `InvalidTransition: ...`.

The bundled router skill and the two domain skills are under `skill/`; the
shared authoritative protocol is `skill/references/review-protocol.md`.
