# Build rubber_stamp as a separate LangGraph application

Status: Superseded
Date: 2026-10-05
Deciders: Patrick Clawson
Consulted: Infrastructure team; rubber_stamp and rubber_ducky project evidence
Informed: Infrastructure team
Spec: https://linear.app/meetalix/project/rubber-stamp-9973c4ed4dde
Supersedes:
Superseded by: https://github.com/meetalix/rubber_stamp/blob/main/docs/adr/2026-10-06-human-authored-adr-filing.md

## Context

The company process requires a Linear spec and one Proposed ADR per
consequential decision before implementation planning. ADR authoring requires
repository and Linear access, discovery of existing decisions and vocabulary,
verification of `path::symbol` anchors, and comparison of serious
alternatives. The author must report missing product facts instead of
inventing them.

rubber_ducky currently provides a reviewer lifecycle, not an authoring agent.
Its automatic reviewer receives canonical review state and has no repository
tools. Folding discovery into that reviewer would combine author and reviewer
responsibilities, violating the required independent-agent boundary.
rubber_ducky's Python, Pydantic, LangChain, LangGraph, SQLite, CLI, and MCP
stack provides proven patterns for typed orchestration and durable agent
workflows, but sharing a repository would couple the author and reviewer
release surfaces.

Should rubber_stamp v1 be its own application and repository, and which
runtime boundary should it adopt?

## Decision drivers

- Keep ADR authorship separate from independent review.
- Reuse the proven rubber_ducky technology and durability patterns.
- Give authoring an independent package, release, and execution boundary.
- Support authenticated Linear, repository, and verification tools.
- Report blocking gaps rather than manufacture product or architecture facts.
- Avoid a hosted network service until scheduling or tenancy requires one.
- Avoid extracting a shared package before stable overlap is demonstrated.

## Decision

Supersession recorded 2026-10-06: the project owner explicitly accepted the
human-authored filing replacement in [rubber_stamp PR #6](https://github.com/meetalix/rubber_stamp/pull/6#pullrequestreview-5432200079).
The historical decision below is retained for traceability, not authority for
new agent-authoring implementation. The evidence-handoff and rubber_ducky ADR
review-mode decisions remain unchanged. Existing code, transports, and journals
are preserved pending separately reviewed retirement.

We will implement rubber_stamp v1 as a separate Python application and package
in its own `meetalix/rubber_stamp` repository.

The application will use:

- Python 3.12+ and Pydantic for typed contracts;
- LangChain for model and tool integration;
- LangGraph for the discovery, evidence, drafting, blocking-gap, and review
  handoff workflow;
- SQLite-backed checkpointing/journaling for resumable local runs;
- a CLI and MCP server as the v1 public interfaces; and
- a fresh author execution context that is separate from rubber_ducky's
  reviewer.

The author workflow will:

- require a Linear project and exact repository revision;
- verify that the spec contains a problem, v1 scope, deferred items, and a
  success measure;
- read repository instructions, context, relevant ADRs, code, and tests;
- create one exact-template Proposed ADR per consequential decision;
- produce the accepted evidence-handoff contract;
- stop on missing or unverifiable facts;
- hand the artifacts to a separate reviewer context; and
- leave human acceptance to GitHub review.

v1 will not introduce a hosted network service, queue, scheduler, or
autonomous acceptance mechanism. rubber_stamp and rubber_ducky will integrate
through the versioned public evidence/review handoff, not internal imports.
They may extract a shared package later, but v1 will not create one before
stable duplicated contracts or implementation are demonstrated.

## Alternatives

### A. Add only a host-agent skill beside rubber_ducky — rejected

This is the obvious shortcut and can use existing host tools. Rejected because
it provides no independently versioned, typed, testable application boundary
for the intended LangGraph authoring workflow.

### B. Add a rubber_stamp package inside the rubber_ducky repository — rejected

This maximizes immediate code reuse. Rejected because it couples author and
reviewer ownership, dependencies, releases, and repository access despite
their required independent responsibilities.

### C. Deploy a standalone hosted rubber_stamp service — rejected

This provides central execution, credentials, and scheduling. Rejected for v1
because CLI/MCP plus local durability satisfy the known workflow without a
network deployment, tenancy model, or queue.

### D. Build a separate LangGraph application with CLI and MCP

This wins because it preserves author/reviewer ownership boundaries while
reusing a proven stack and avoiding premature hosted-service and shared-package
complexity.

## Consequences

- rubber_stamp has its own repository, package lifecycle, dependencies, and
  release surface.
- The team operates a second CLI/MCP application and SQLite state format.
- Similar configuration, persistence, and MCP patterns may initially exist in
  both repositories.
- Integration must use versioned public contracts rather than private imports.
- A future shared package requires its own decision and migration cost.
- A hosted service remains deferred even though the application is separate.
- This decision does not choose the evidence schema, reviewer integration,
  ADR review domain, plan authoring, or protocol changes.

## Confirmation

A later reviewer or agent checks compliance by:

- verifying rubber_stamp is packaged and tested from its own repository;
- verifying the author workflow is a LangGraph graph with typed Pydantic state,
  resumable SQLite checkpoints, and CLI/MCP entry points;
- verifying rubber_stamp has no rubber_ducky reviewer-generation or private
  module dependency;
- verifying missing Linear scope or unverifiable anchors stop authoring with a
  blocker;
- verifying the produced ADRs use the company template and remain Proposed;
- verifying independent review runs in a distinct context; and
- verifying no hosted service, queue, shared package, or acceptance operation
  is introduced in the v1 plan.

## Anchors

- `skill/rubber_ducky/SKILL.md::Rubber Ducky` — the existing workflow routes
  reviewed artifacts and does not perform ADR discovery.
- `src/rubber_ducky/core/adapters.py::ReviewerAdapter` — the automatic reviewer
  has no tools and must remain separate from author discovery.
- `src/rubber_ducky/core/service.py::ReviewService` — current durable state
  belongs to review orchestration, not ADR authoring.

## Revisit when

Revisit repository/package sharing when both applications contain a stable,
semantically identical contract or implementation whose duplication costs
more than coordinating releases. Revisit hosted deployment when multiple teams
need queued or scheduled authoring, centrally managed credentials/state,
unattended execution, or a network API.

## Open questions

None. The evidence handoff and review-mode
contracts are decided separately.
