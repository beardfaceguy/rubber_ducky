---
name: rubber_ducky
description: route an agent-to-agent review request to code or plan review
disable-model-invocation: false
---

# Rubber Ducky — Review Router

Rubber Ducky runs formal, durable agent-to-agent reviews using one shared
round-based protocol (reviewer responses, worker rebuttals, up to three rounds,
then human escalation). It reviews two kinds of artifact, each with its own
skill. This skill only routes; it performs no review itself.

## Choose the domain

- **Code or a diff** — an actual patch, changed files, or code under review →
  use **`rubber_ducky_code`**. The reviewed payload is the real diff.
- **A proposed plan or design** — an objective with ordered steps and
  acceptance criteria, not yet implemented → use **`rubber_ducky_plan`**. The
  reviewed payload is a structured plan document.

Decide by the artifact the caller supplies, not by the task's subject:

- If the caller hands you code/diff text, route to `rubber_ducky_code`.
- If the caller hands you a plan (objective + steps + acceptance), route to
  `rubber_ducky_plan`.
- If both are present, review the plan first (`rubber_ducky_plan`), then the
  implementing diff (`rubber_ducky_code`) as a separate review.
- If the artifact is ambiguous, ask the operator which one they want reviewed
  before starting.

## After routing

Read the chosen skill's `SKILL.md` and follow it. The two domains share:

- the same protocol semantics (roles, IDs `B*`/`S*`/`R*`, verdicts APPROVE /
  REVISE / ESCALATE, the three-round limit, and escalation to the human);
- the same reviewer configuration and credential precedence
  (`~/.config/rubber_ducky/`);
- the same durable audit log under `rubber_ducky/` at the workspace root.

They differ only in the tool surface:

- `rubber_ducky_code` → the `rubber-ducky-code` CLI and `rubber_ducky_code_*` MCP tools;
  payload is a diff.
- `rubber_ducky_plan` → the `rubber-ducky-plan` CLI and `rubber_ducky_plan_*` MCP tools;
  payload is a plan document.

Do not mix the two tool surfaces within a single review conversation.
