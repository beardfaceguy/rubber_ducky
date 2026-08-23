---
name: agent-review
description: formal agent-to-agent code review
disable-model-invocation: false
---

# Agent Review

Use this skill for formal agent-to-agent reviews of code or diffs. Conceptual
and high-level plan reviews are deferred to a future protocol version.

## Required first step

Read the complete [Review Protocol](references/review-protocol.md). It remains
the authority for roles, message content, verdicts, rounds, and escalation.

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
`AGENT_REVIEW_REVIEWER_MODEL`, then `agent_review/config.json`. Credentials are
read from the configured environment-variable name and must never be written
to input JSON or audit logs. Provider and model must come together from one
precedence source; do not mix values across sources.

When acting as the reviewer, never write files or invoke CLI/MCP write
operations. Return only the protocol-formatted Review Response to the worker.
The worker records it with `respond`.

## Fallback

If `agent-review` is unavailable, follow the Markdown protocol manually. The
worker owns `agent_review/<task-id>-<short-slug>.md`, records every message
verbatim, enforces the three-round limit, and escalates unresolved deadlocks.

Do not mix CLI and manual logging within the same review unless recovering from
a CLI failure and the operator explicitly chooses the fallback.
