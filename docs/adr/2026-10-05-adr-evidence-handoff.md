# Hand independent ADR reviewers an immutable evidence bundle and read-only source access

Status: Accepted
Date: 2026-10-05
Deciders: Patrick Clawson
Consulted: Infrastructure team; rubber_stamp and rubber_ducky project evidence
Informed: Infrastructure team
Spec: https://linear.app/meetalix/project/rubber-stamp-9973c4ed4dde
Supersedes:
Superseded by:

## Context

An ADR reviewer must be able to challenge the author's facts, alternatives,
and scope without inheriting the author's private context or trusting an
author-written summary. The current rubber_ducky reviewer receives canonical
review state, has no tools, and cannot open repository anchors independently.
Its models validate message structure, not factual correctness or citation
relevance.

Free-form prose alone does not establish which Linear scope was read, which
revision was inspected, whether cited symbols exist, or whether a revised ADR
still matches the evidence reviewed. Giving the reviewer the entire author
conversation would provide more context, but it would weaken independence and
still would not identify a stable, reviewable evidence boundary.

What evidence contract and source access will let an independent reviewer
verify a Proposed ADR against the spec and current repository?

## Decision drivers

- Let the reviewer independently reopen every material source.
- Bind one review attempt to an exact spec snapshot, repository revision, and
  artifact version.
- Preserve author/reviewer context separation.
- Fail closed when required evidence is absent or unverifiable.
- Keep human acceptance and private reasoning outside the handoff.
- Provide a stable contract that rubber_ducky can persist without adopting
  dataset capture.

## Decision

We will hand each independent ADR reviewer a versioned, immutable
`AdrEvidenceHandoff` plus read-only access to the named repository revision.

The handoff will contain:

- `schema_version`;
- `spec`: Linear URL/identifier, problem, in-scope behaviors, deferred items,
  success measure, and unresolved product questions;
- `repository`: URL, exact revision, and captured working-tree state;
- `sources_read`: repository instructions, context/glossary, existing ADRs,
  and opened `path::symbol` anchors with their purpose;
- `findings`: factual claims, supporting anchors, verification commands and
  results, and explicit uncertainty;
- `alternatives`: the obvious shortcut, serious alternatives, recommendation,
  and decision-driver tradeoffs;
- `artifacts`: Proposed ADR paths and SHA-256 hashes of the exact reviewed
  bytes; and
- `blockers`: missing facts, unavailable access, and failed verification.

The handoff will be serialized once as a UTF-8 JSON file. Those exact stored
bytes will be SHA-256 hashed before the review starts. Persisted protocol state
will reference the stored artifact path and digest; it will not attempt to
reconstruct identical bytes by reparsing and reserializing the JSON. A review
request and every author revision will name the handoff hash, ADR artifact
hash, and repository revision it uses. Changing evidence or the ADR creates
and stores a new immutable version rather than mutating the reviewed bundle.

The reviewer will run in a separate context with read-only repository/history
access and permission to run non-mutating verification. It will receive the
handoff and full ADR artifact, but not the author's private reasoning or
credentials. It must open each material anchor before relying on it. Missing
or contradictory evidence becomes a blocking concern. The reviewer context
will return a response to the author/caller; only that caller may submit the
response to rubber_ducky. The reviewer will not invoke rubber_ducky `status` or
response operations because those may write repository-local durable state.

GitHub remains the system of record for ADR status. The handoff contains no
human-acceptance field, and automated review cannot update the ADR status.

## Alternatives

### A. Send only the ADR and an author-written prose summary — rejected

This is the obvious shortcut. Rejected because the reviewer cannot bind claims
to a revision, verify source coverage, or detect omitted evidence reliably.

### B. Give the reviewer the full author conversation — rejected

This maximizes context. Rejected because it couples the reviewer to mutable
private reasoning, weakens independent assessment, and still lacks a stable
artifact/evidence identity.

### C. Give tools directly to rubber_ducky's automatic ReviewerAdapter — rejected

This could let the configured model inspect sources. Rejected for v1 because
it changes the existing no-tools security boundary and provider execution
model. A separate read-only reviewer context can produce a validated response
for the existing caller-submitted path.

### D. Use an immutable evidence bundle plus independent read-only access

This wins because the reviewer gets both a bounded contract and the ability to
verify it independently, while private reasoning and human acceptance remain
outside the payload.

## Consequences

- rubber_stamp must construct and validate a structured evidence bundle.
- Review attempts become reproducible against explicit spec, source, and
  artifact identities.
- Reviewers incur the cost of reopening material anchors rather than trusting
  summaries.
- SHA-256 establishes content identity, not factual truth; semantic review is
  still required.
- Repository access controls must support a separate read-only context.
- The contract adds stored metadata but does not authorize conversation
  datasets, raw model-output retention, or external publishing.
- This decision does not choose the reviewed-artifact domain or PLAN.md
  behavior.

## Confirmation

A later reviewer or agent checks compliance by:

- validating that every handoff has all required sections and an exact
  repository revision;
- reading the exact stored handoff `.json` bytes and recomputing both handoff
  and ADR SHA-256 values without reserializing parsed JSON;
- opening every material `path::symbol` anchor in the named revision;
- verifying failed or unavailable evidence appears under blockers;
- running the reviewer in a read-only checkout/sandbox whose tool allowlist has
  no filesystem, GitHub, Linear, or rubber_ducky mutation operations, and
  confirming an attempted sentinel-file write is denied;
- confirming the author/caller, not the reviewer context, submits the returned
  protocol response;
- confirming author-private conversation and credentials are absent; and
- confirming no automated operation changes ADR status.

## Anchors

- `src/rubber_ducky/core/models.py::Concern` — current concerns are free text
  without structured evidence.
- `src/rubber_ducky/core/adapters.py::_participant_messages` — the automatic
  reviewer receives canonical state and an explicit no-tools instruction.
- `src/rubber_ducky/core/adapters.py::ReviewerAdapter` — current generation
  validates structure but cannot independently inspect source.
- `src/rubber_ducky/core/service.py::ReviewService.submit` — externally
  produced validated reviewer events can already enter the durable lifecycle.
- `src/rubber_ducky/core/audit.py::ArtifactEvidence` — existing artifact
  identity uses relative paths and SHA-256.

## Revisit when

Revisit if a trusted repository service can attest anchors and verification
results directly, or if approved data-governance requirements mandate a
different evidence-retention boundary.

## Open questions

None for this proposed decision. Concrete model/module names may be refined in
the post-acceptance plan without changing the handoff fields or ownership.
