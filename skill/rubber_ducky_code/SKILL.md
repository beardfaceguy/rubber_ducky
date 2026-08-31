---
name: rubber_ducky_code
description: formal agent-to-agent code review
disable-model-invocation: false
---

# Rubber Ducky — Code Review

Use this skill for formal agent-to-agent reviews of code or diffs. To review a
proposed plan or design instead, use `rubber_ducky_plan`; the `rubber_ducky`
router chooses between them.

## Required first step

Read the complete [Review Protocol](../references/review-protocol.md). It is the
shared authority for roles, message content, verdicts, rounds, and escalation.

## Payload

This domain's reviewed artifact is the actual code diff. In the shared
protocol's message formats, the artifact sections take these headings:

- Review Request → `### Relevant Code / Diff` — the full diff or code under
  review. Never summarize code; include it.
- Rebuttal → `### Revised Code / Diff` — the actual revised code when any
  blocking concern is ACCEPTed, otherwise `Unchanged — see Review Request.`

## Primary workflow

When acting as the worker, use the `agent-review` CLI from the workspace root:

```text
agent-review start <thread-id> <slug> --input <request.json>
agent-review status <thread-id>
agent-review review <thread-id> <event-id> [--provider P --model M]
agent-review respond <thread-id> <event-id> --input <response.json>
agent-review rebut <thread-id> <event-id> --input <rebuttal.json>
agent-review resume <thread-id> <event-id> --input <summary.json>
```

For Vikunja work, use the task's absolute numeric database ID as `task_id` and
the audit filename prefix (for example `1382-description.md`). Never use the
project-relative identifier such as `#17`.

Pass `--workspace <path>` before the command when the workspace is not the
current directory. Use `--input -` to read JSON from stdin. Treat exit code 0
as success; all command results and errors are JSON. `status` may repair a
lagging durable checkpoint and therefore requires workspace write access.

Exit codes:

- `0` — success
- `2` — invalid command or input
- `3` — review not found
- `4` — persistence conflict or invalid transition
- `5` — unexpected internal failure

If the MCP server is configured, workers may use the equivalent
`agent_review_start`, `agent_review_status`, `agent_review_generate`,
`agent_review_respond`, `agent_review_rebut`, and `agent_review_resume` tools.
Reuse the same thread and event IDs when switching between CLI and MCP; both
call the same durable service.

Reviewer provider and model are never hard-coded. Resolution order is explicit
CLI/MCP values, then `AGENT_REVIEW_REVIEWER_PROVIDER` plus
`AGENT_REVIEW_REVIEWER_MODEL`, then the global config. When
`XDG_CONFIG_HOME` is absolute, use
`$XDG_CONFIG_HOME/agent_review/config.json`; otherwise use
`~/.config/agent_review/config.json`. Credentials are read from the configured
environment-variable name and must never be written to input JSON or audit
logs. Provider and model must come together from one precedence source; do not
mix values across sources.

Credential values may be placed in the sibling global `.env` file
(`$XDG_CONFIG_HOME/agent_review/.env` when XDG is absolute, otherwise
`~/.config/agent_review/.env`). Require mode `600` on POSIX. Real process
environment variables override `.env`, and project `.env` files are ignored.
Treat global `.env` as credential input only: provider/model/XDG settings in it
are ignored for configuration selection, and variable interpolation is off.

When acting as the reviewer, never write files or invoke CLI/MCP write
operations. Return only the protocol-formatted Review Response to the worker.
The worker records it with `respond`.

## Fallback

If `agent-review` is unavailable, follow the Markdown protocol manually. The
worker owns `agent_review/<task-id>-<short-slug>.md`, records every message
verbatim, enforces the three-round limit, and escalates unresolved deadlocks.

Do not mix CLI and manual logging within the same review unless recovering from
a CLI failure and the operator explicitly chooses the fallback.
