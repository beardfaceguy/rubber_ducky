---
name: rubber_ducky_plan
description: formal agent-to-agent plan review
disable-model-invocation: false
---

# Rubber Ducky — Plan Review

Use this skill for formal agent-to-agent reviews of a proposed plan or design.
To review code or a diff instead, use `rubber_ducky_code`; the `rubber_ducky`
router chooses between them. Plan and code review share the same round-based
protocol, verdicts, and escalation rules — only the reviewed payload differs.

## Required first step

Read the complete [Review Protocol](../references/review-protocol.md). It is the
shared authority for roles, message content, verdicts, rounds, and escalation.

## Payload

This domain's reviewed artifact is the structured plan below. In the shared
protocol's message formats, the artifact sections take these headings:

- Review Request → `### Proposed Plan` — the full plan document. Never
  summarize; include it.
- Rebuttal → `### Revised Plan` — the revised plan when any blocking concern is
  ACCEPTed, otherwise `Unchanged — see Review Request.`

## Plan document shape

The caller passes the plan content itself (never a link) as the `plan` field of
the request JSON:

```json
{
  "task_id": "AR-8",
  "title": "Add plan review domain",
  "proposed_solution": "Mirror the code domain for plans.",
  "plan": {
    "objective": "Ship durable plan review.",
    "context": "Reuse the shared protocol engine.",
    "steps": [
      {
        "id": "P1",
        "description": "Define the plan document schema.",
        "rationale": "Callers need a stable structure.",
        "acceptance": ["PlanDocument validates a minimal plan."]
      }
    ],
    "acceptance_criteria": ["tests/plan green"],
    "risks": ["Schema too rigid for large plans."]
  }
}
```

Step IDs are `P1`, `P2`, … `steps` and `acceptance_criteria` each need at least
one entry; `context`, `rationale`, per-step `acceptance`, and `risks` are
optional.

## Primary workflow

When acting as the worker, use the `plan-review` CLI from the workspace root:

```text
plan-review start <thread-id> <slug> --input <request.json>
plan-review status <thread-id>
plan-review review <thread-id> <event-id> [--provider P --model M]
plan-review respond <thread-id> <event-id> --input <response.json>
plan-review rebut <thread-id> <event-id> --input <rebuttal.json>
plan-review resume <thread-id> <event-id> --input <summary.json>
```

The `rebut` input carries a `revised_plan` field: a full plan document when any
concern is ACCEPTed, or `null` when nothing changed.

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
`plan_review_start`, `plan_review_status`, `plan_review_generate`,
`plan_review_respond`, `plan_review_rebut`, and `plan_review_resume` tools.
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

If `plan-review` is unavailable, follow the Markdown protocol manually. The
worker owns `agent_review/<task-id>-<short-slug>.md`, records every message
verbatim, enforces the three-round limit, and escalates unresolved deadlocks.

Do not mix CLI and manual logging within the same review unless recovering from
a CLI failure and the operator explicitly chooses the fallback.
