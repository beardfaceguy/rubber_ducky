# Add an ADR review mode while keeping human acceptance outside rubber_ducky

Status: Accepted
Date: 2026-10-05
Deciders: Patrick Clawson
Consulted: Infrastructure team; rubber_stamp and rubber_ducky project evidence
Informed: Infrastructure team
Spec: https://linear.app/meetalix/project/rubber-ducky-alix-port-4201a2d28a3c
Supersedes:
Superseded by:

## Context

rubber_ducky currently shares one Review Protocol v1.3 lifecycle across code
and plan review modes. Each mode supplies typed request, rebuttal, state, service,
CLI, MCP, and skill bindings. The shared core already provides deterministic
rounds, caller-submitted reviewer responses, durable replay, artifact hashing,
audit projection, and human escalation.

Neither existing mode represents a company ADR. Code review requires a diff.
Plan review requires a custom `PlanDocument` that omits required company
PLAN.md sections and is not an ADR template. Treating ADR Markdown as either
payload would blur public contracts and make future compatibility unclear.
Keeping ADR negotiation entirely outside rubber_ducky would preserve current
interfaces but duplicate its durable concern/rebuttal/escalation behavior.
Human acceptance is already a fixed product boundary: it remains an explicit
GitHub action and cannot be performed by this review mode.

Should rubber_ducky add an ADR-specific review mode on the shared
v1.3 core for the automated negotiation that precedes human acceptance?

## Decision drivers

- Reuse the proven v1.3 lifecycle, replay, audit, and escalation behavior.
- Give ADR review an explicit artifact and evidence contract.
- Accept responses from an independent reviewer with read-only source access.
- Preserve existing code and plan APIs and saved sessions.
- Keep protocol approval separate from human ADR acceptance.
- Avoid premature generic-document abstraction or protocol migration.

## Decision

We will add a narrow ADR review mode to rubber_ducky after the
evidence-handoff ADR is accepted.

The mode will add:

- `AdrReviewRequest`, carrying the full exact-template Proposed ADR Markdown,
  the ADR artifact hash, repository revision, and an `AdrEvidenceReference`
  containing only the stored evidence artifact's relative path, SHA-256
  digest, and schema version;
- `AdrRebuttal`, carrying the full revised ADR Markdown, its new artifact hash,
  and a new `AdrEvidenceReference` when the author accepts a concern;
- `AdrReviewState` and `AdrReviewService`, bound to the existing shared
  protocol v1.3 reducer, persistence, checkpoints, and audit;
- audit headings `Proposed ADR` and `Revised ADR`, with `.md` artifacts and
  separately stored exact `.json` evidence artifacts;
- CLI `rubber-ducky-adr`; and
- MCP tools `rubber_ducky_adr_start`, `status`, `respond`, `rebut`, and
  `resume`, plus router-skill support.

The ADR review mode will not expose automatic reviewer generation in v1. Its
reviewer response will be produced in a separate read-only repository context
and returned to the author/caller, which submits it through the validated
response path. The reviewer context will not invoke `status`, `respond`, or
other rubber_ducky operations because they may write durable local state. This
avoids granting tools to the current automatic `ReviewerAdapter` and keeps
reviewer access read-only.

Protocol `APPROVE` will mean only that the automated author/reviewer exchange
has no open blocking concerns. It will not change `Status: Proposed`, create a
human review, or authorize a PLAN.md. GitHub review and an explicit human edit
to the ADR file remain the only acceptance mechanism.

The code and plan modes, protocol v1.3, their saved sessions, and their
public interfaces will remain unchanged. Candidate protocol changes, company
PLAN.md review, TASKS.md, datasets, and raw model-output retention are outside
this decision.

## Alternatives

### A. Pass ADR Markdown through the existing plan mode — rejected

This is the obvious shortcut. Rejected because `PlanDocument` is a different
typed contract, does not represent the company ADR template, and would make
plan compatibility depend on hidden ADR-specific behavior.

### B. Add a generic document-review mode — rejected

This could support ADRs and future artifact types. Rejected because code and
plan already demonstrate real artifact-specific validation, while hypothetical
document types do not justify a broad abstraction.

### C. Keep ADR review outside rubber_ducky — rejected

This avoids repository changes. Rejected because it would duplicate stable
concern IDs, bounded rebuttals, replay, artifact hashes, audit logs, and
escalation or leave those guarantees absent.

### D. Add a dedicated ADR review mode on the shared v1.3 core

This wins because it keeps a small, explicit public contract and reuses the
deep lifecycle module without changing existing modes or conflating human
acceptance.

## Consequences

- rubber_ducky gains a third public review mode and five ADR MCP tools.
- The shared CLI builder or ADR CLI binding must support a mode with no
  automatic `review` command.
- The unified server and router skill must include the ADR tools/mode.
- ADR request/rebuttal checkpoint types join the explicit trusted allowlist.
- Existing code and plan saved sessions remain readable because protocol and
  existing event models do not change.
- Independent review requires an external read-only reviewer context; the
  built-in provider factory is not used for ADR generation in v1.
- Human acceptance remains auditable in GitHub but is not duplicated in
  rubber_ducky state.
- This decision adds implementation and test surface for a third review mode.

## Confirmation

A later reviewer or agent checks compliance by:

- verifying ADR request/rebuttal models preserve exact Markdown bytes and
  persist only the evidence artifact path/digest/schema reference, not a
  reparsed handoff object;
- comparing each evidence reference with the separately stored exact `.json`
  bytes and their SHA-256 without reserializing parsed JSON;
- running an end-to-end externally produced response/rebuttal/escalation flow
  through CLI and MCP;
- verifying no `rubber_ducky_adr_generate` tool or ADR CLI `review` command is
  exposed in v1;
- verifying an automated `APPROVE` leaves the ADR file `Status: Proposed`;
- rerunning existing code/plan replay, CLI, MCP, and output-schema tests;
- reopening pre-change code and plan SQLite sessions successfully; and
- verifying datasets, protocol candidate changes, and PLAN.md replacement are
  absent from the implementation plan.

## Anchors

- `src/rubber_ducky/core/models.py::ReviewRequestBase` — reviewed-artifact
  modes extend the shared request contract.
- `src/rubber_ducky/core/lifecycle.py::ReviewStatus` — the existing terminal
  states are sufficient for automated ADR negotiation and escalation.
- `src/rubber_ducky/core/service.py::ReviewService.submit` — caller-produced
  reviewer events already use the durable validated path.
- `src/rubber_ducky/code/models.py::CodeReviewState` — existing mode binding
  pattern for concrete request/rebuttal checkpoint reconstruction.
- `src/rubber_ducky/plan/models.py::PlanDocument` — the current plan payload is
  not a company ADR and must not be reused as one.
- `src/rubber_ducky/code/mcp_server.py::register_tools` — existing public
  per-mode tool registration pattern.
- `src/rubber_ducky/mcp_server.py::server` — unified server currently mounts
  code and plan modes.

## Revisit when

Revisit if multiple non-code/non-plan artifact types demonstrate a stable
shared document contract, or if a separately accepted decision gives an
automatic reviewer safe read-only source tools.

## Open questions

None. Concrete class/module names may be refined in
the accepted plan while preserving the review-mode and public-interface boundary.
