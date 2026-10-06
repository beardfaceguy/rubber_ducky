# PLAN — rubber_ducky ADR review mode

Status: Approved
Date: 2026-10-05
Spec: https://linear.app/meetalix/project/rubber-ducky-alix-port-4201a2d28a3c
ADRs: 2026-10-05-rubber-ducky-adr-review-mode, 2026-10-05-adr-evidence-handoff (accepted)
Branch: infra-107-adr-review-mode-plan

## Goal

rubber_stamp can submit exact Proposed ADR and evidence bytes to a durable
rubber_ducky ADR review mode; an independent read-only reviewer can return
responses, the author can revise or rebut, and unresolved concerns can
escalate without changing human ADR status. Existing code and plan review
behavior and saved sessions remain compatible.

## In scope

1. Add typed ADR request, evidence-reference, rebuttal, state, service, and
   checkpoint bindings on the unchanged Review Protocol v1.3 core.
2. Store exact Proposed/Revised ADR Markdown and exact evidence JSON bytes as
   separate hashed audit artifacts; validate all supplied identities.
3. Accept externally produced reviewer responses through the existing durable
   response lifecycle; do not generate ADR reviewer responses.
4. Add `rubber-ducky-adr` CLI start/status/respond/rebut/resume commands.
5. Add five structured MCP tools:
   `rubber_ducky_adr_start`, `status`, `respond`, `rebut`, and `resume`.
6. Add ADR mode routing and calling-agent guidance while retaining code and
   plan modes.
7. Verify replay, checkpoint reopen, audit recovery, idempotency, output
   schemas, and compatibility with pre-change code/plan sessions.
8. Preserve the external human acceptance gate: automated approval leaves the
   reviewed ADR bytes and GitHub status unchanged.

## Out of scope

- Automatic ADR reviewer generation or repository tools on `ReviewerAdapter`.
- Changes to Review Protocol v1.3 rounds, events, verdicts, or terminal states.
- Replacing, removing, or converting the existing plan review mode.
- Company PLAN.md or TASKS.md authoring/review.
- Changes to code review behavior or public contracts.
- Human accept/reject/supersede operations.
- Review-conversation datasets, raw model-output retention, training, export,
  or publication.
- A generic document-review abstraction.

An implementer does not build an out-of-scope item from this plan.

## Design rules

- The ADR mode lives under `src/rubber_ducky/adr/` (new), mirroring the deep
  public boundary of `code/` and `plan/`.
- Reuse `ReviewRequestBase`, `RebuttalBase`, `ReviewState`, `ReviewService`,
  reducer, persistence, replay, and checkpoint factories without changing
  protocol v1.3 event semantics.
- Preserve ADR and evidence text exactly. Validate non-whitespace content
  without applying `strip_whitespace` transformations.
- `AdrEvidenceReference` stores only schema version, audit-relative path, and
  SHA-256. Protocol state never stores a parsed evidence object.
- CLI/MCP start and revised-rebuttal transports carry evidence JSON text
  separately from protocol event models. The service writes its exact UTF-8
  bytes, hashes the stored file, and then validates the reference.
- Evidence JSON is parsed before persistence to require `schema_version`,
  `spec`, `repository`, `sources_read`, `findings`, `alternatives`,
  `artifacts`, and `blockers`. The repository snapshot and matching ADR artifact
  entry must bind the same repository revision and ADR hash as the protocol
  request/rebuttal. The handoff's own hash exists only in the external protocol
  request/reference, never inside the hashed handoff bytes.
- Evidence paths are service-generated inside the review artifact directory;
  caller-provided filesystem paths are never trusted.
- Accepted concerns require a full revised ADR and a newly stored evidence
  artifact/reference. Dispute/clarify-only rebuttals reuse the current
  artifact identities.
- The independent reviewer never invokes rubber_ducky tools. The author/caller
  submits the returned `ReviewResponse`.
- There is no ADR `generate` command/tool. Existing code/plan generation stays
  enabled and unchanged.
- Protocol `APPROVE` changes only durable review state. No code path edits ADR
  files, GitHub reviews, or `Status: Proposed`.
- Start and rebuttal prevalidate exact bytes, references, status, and legal
  transitions; then write artifacts before inserting the request/event into
  the journal. Byte-identical orphaned artifacts are recoverable on retry.
- `AuditLog.store_artifact` (new) exclusively writes or recovers bytes and
  returns `ArtifactEvidence`. `AuditLog.append_prepared` (new) appends rendered
  protocol text and metadata for already stored artifacts without writing
  another artifact. Existing `AuditLog.append` delegates to these seams so code
  and plan filenames/output remain byte-for-byte compatible.
- `AdrReviewService` overrides inherited `start`, `submit`, and
  `generate_review` bypasses. Plain start and generate always reject;
  plain submit accepts only `ReviewResponse` and `EscalationSummary` and rejects
  `AdrRebuttal`. ADR requests and rebuttals must use the evidence-aware methods.
- Existing serializer allowlists are extended only with ADR types; existing
  event JSON and checkpoint models are not rewritten.
- The unified server and router add ADR mode without renaming existing tools,
  scripts, imports, or skill directories.

## Interfaces

```python
class AdrEvidenceReference(ProtocolModel):
    schema_version: Literal["1.0"]
    relative_path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

class AdrReviewRequest(ReviewRequestBase):
    adr_markdown: str
    adr_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    repository_revision: NonEmptyText
    evidence: AdrEvidenceReference

class AdrRebuttal(RebuttalBase):
    revised_adr_markdown: str | None
    revised_adr_sha256: str | None
    repository_revision: str
    revised_evidence: AdrEvidenceReference | None

class AdrReviewState(ReviewState):
    request: AdrReviewRequest
    rebuttals: tuple[AdrRebuttal, ...]

class AdrStartInput(BaseModel):
    request: AdrReviewRequest
    evidence_json: str

class AdrRebuttalInput(BaseModel):
    rebuttal: AdrRebuttal
    evidence_json: str | None
```

`AdrReviewRequest.payload_text()` returns `adr_markdown` exactly and uses
artifact suffix `.md`. `AdrRebuttal.revised_text()` returns exact revised
Markdown or the shared unchanged sentinel. A model validator requires
`revised_adr_markdown`, its hash, a new evidence reference, and evidence JSON
transport whenever any blocking concern is `ACCEPT`. Every request and
rebuttal requires the same repository revision; changing source revision
requires a new review.

`AdrReviewService.start_with_evidence(thread_id, slug, start_input)` performs:

1. decode exact CLI/MCP inputs as strict UTF-8 without newline normalization;
2. validate identifiers, `Status: Proposed`, every ADR template section, and
   required evidence sections;
3. compute ADR/evidence hashes in memory and cross-check the evidence
   repository snapshot and ADR artifact entry;
4. create/recover the audit identity and preflight generated artifact paths;
5. write `round-1-request-evidence.json` and `round-1-request.md` with
   exclusive-create/byte-identical recovery;
6. create/recover the journal request referencing those artifacts;
7. append/repair the human-readable audit projection; and
8. reconcile the checkpoint.

`submit_adr_rebuttal` follows the same journal/audit/checkpoint ordering and
first dry-runs the transition and validates revised `Status: Proposed`,
template sections, hashes, evidence sections, and review-input bindings. It
stores `round-N-<event-id>-evidence.json` and
`round-N-<event-id>-rebuttal.md` before the changed rebuttal event becomes
canonical, then projects audit text and reconciles the checkpoint. Recovery
reuses byte-identical orphaned artifacts and rejects conflicting bytes.

The service exposes only:

```python
start_with_evidence(thread_id, slug, start_input)
status(thread_id)
submit_review_response(thread_id, event_id, response)
submit_adr_rebuttal(thread_id, event_id, rebuttal_input)
resume_escalation(thread_id, event_id, summary)
```

Inherited `start` and `generate_review` raise `UnsupportedOperation`. Inherited
generic `submit` dispatches only reviewer responses and escalation summaries;
an `AdrRebuttal` raises `UnsupportedOperation` with guidance to use
`submit_adr_rebuttal`.

ADR audit projection calls `append_prepared` with the prewritten ADR
`ArtifactEvidence` and `evidence.path`, `evidence.sha256`, and
`evidence.schema_version` metadata. It therefore does not invoke the legacy
round-based artifact writer a second time. Code and plan `append` calls retain
their existing `round-1-review-request.*` and `round-N-rebuttal.*` names.

CLI file arguments are loaded with `Path.read_bytes()` and strict UTF-8 decode;
tests preserve LF/CRLF, non-ASCII text, and presence/absence of a final newline.
MCP strings are hashed and stored as their direct UTF-8 encoding.

CLI:

```text
rubber-ducky-adr --workspace PATH start THREAD SLUG \
  --input request.json --evidence evidence.json
rubber-ducky-adr --workspace PATH status THREAD
rubber-ducky-adr --workspace PATH respond THREAD EVENT --input response.json
rubber-ducky-adr --workspace PATH rebut THREAD EVENT \
  --input rebuttal.json [--evidence evidence.json]
rubber-ducky-adr --workspace PATH resume THREAD EVENT --input summary.json
```

MCP:

```text
rubber_ducky_adr_start(workspace, thread_id, slug, request, evidence_json)
rubber_ducky_adr_status(workspace, thread_id)
rubber_ducky_adr_respond(workspace, thread_id, event_id, response)
rubber_ducky_adr_rebut(
    workspace, thread_id, event_id, rebuttal, evidence_json=None
)
rubber_ducky_adr_resume(workspace, thread_id, event_id, summary)
```

Every result contains `thread_id`, typed `AdrReviewState`, and
`expected_event`. MCP failures retain the current `TypeName: message`
`ToolError` contract. CLI exit codes remain 0 success, 2 input/config, 3
missing review, 4 persistence/transition conflict, and 5 unexpected failure.

Packaging adds:

```text
rubber-ducky-adr = "rubber_ducky.adr.cli:run"
rubber-ducky-adr-mcp = "rubber_ducky.adr.mcp_server:run"
```

The unified `rubber-ducky-mcp` registers code, plan, and ADR tools. The router
skill routes exact company ADR Markdown/evidence to `rubber_ducky_adr`; it does
not route ADRs through plan mode.

## Error and edge behavior

| Condition | Result |
| --- | --- |
| ADR Markdown is empty/whitespace | Input validation error; no review/artifact |
| ADR status is not `Proposed` at start | Input validation error; rubber_ducky never reviews accepted/rejected artifacts as new requests |
| Evidence JSON is empty or invalid JSON | Input validation error; no canonical event |
| Evidence schema version/required section differs from reference/contract | Input validation error |
| Evidence repository snapshot or ADR artifact entry does not bind the supplied ADR hash and repository revision | Input validation error |
| Stored evidence bytes do not match supplied SHA-256 | `ArtifactConflict`; no event/checkpoint advance |
| Stored ADR bytes do not match supplied SHA-256 | `ArtifactConflict`; no event/checkpoint advance |
| Evidence relative path escapes review artifacts | Input validation error |
| Existing artifact has identical bytes after retry | Reuse artifact and continue recovery |
| Existing artifact has different bytes | `ArtifactConflict`; fail closed |
| Revised ADR is whitespace-only, lacks exact template sections, or changes status away from Proposed | Input validation error; no event/artifact |
| ACCEPT lacks revised ADR, hash, repository revision, evidence reference, or evidence bytes | Input validation error |
| Rebuttal names a different repository revision | Input validation error; caller starts a new review |
| DISPUTE/CLARIFY supplies replacement evidence | Input validation error; unchanged rebuttal reuses prior identities |
| Reviewer attempts ADR generation | No command/tool exists |
| Internal caller invokes inherited plain start/generate or submits AdrRebuttal through generic submit | `UnsupportedOperation`; no state/artifact change |
| Reviewer context attempts filesystem/GitHub/Linear/rubber_ducky mutation | The rubber_stamp-owned read-only sandbox denies it before a response is returned |
| Reviewer response is wrong round or omits open blockers/points | Existing `InvalidTransition` behavior |
| Protocol reaches `APPROVED` | Return approved state; do not edit ADR/GitHub status |
| Round three remains blocked | Existing final-position and escalation flow |
| Open pre-change code/plan review | Existing concrete state and checkpoint allowlists reconstruct unchanged |
| ADR status/session is queried after process restart | Replay journal, validate/repair checkpoint, return canonical state |

The ADR CLI maps `ArtifactConflict` to existing exit code 4 without changing
the generic code/plan CLI exception mapping. MCP continues to return
`ArtifactConflict: ...` as `ToolError`.

## Rollout

No database migration or feature flag is required because the change adds new
mode types without changing existing event schemas. Check in pre-change
code/plan SQLite and checkpoint fixtures and prove the new version reopens
them. First install the new package version in the project owner's editable
local tool environment and register the five ADR tools. Dogfood one full
rubber_stamp-generated review: its separate read-only reviewer must reopen
every anchor, fail a sentinel write, return a response to the caller, and never
invoke rubber_ducky itself. Exercise rebuttal, approval or escalation, process
restart, exact-byte cases, artifact re-verification, and every rejected
start/submit/generate bypass. Snapshot existing and new CLI help, MCP tool sets,
and output schemas before wider use.

Rollback reinstalls the previous package version and MCP executable
configuration. ADR sessions become temporarily unavailable until the ADR-mode
version is restored; their SQLite/artifact data is retained. Code and plan
operation remains available and compatible throughout rollback.

## Risks

- **Transporting evidence as a JSON string may be large** — keep evidence to
  structured claims, anchors, and bounded summaries; measure before adding
  streaming or external storage.
- **Exact-byte preservation can be lost by model validators** — use
  non-stripping fields and byte-for-byte artifact tests.
- **Two artifacts per request/revision complicate crash recovery** — add
  focused journal/audit/checkpoint fault-injection tests for every side-effect
  boundary.
- **CLI parameterization could break code/plan generation** — defaults preserve
  current commands and snapshot existing help/output contracts.
- **Users may mistake protocol approval for ADR acceptance** — name the result
  automated approval in docs and test that no GitHub/file status write exists.
- **New trusted checkpoint types increase deserialization surface** — extend
  the explicit allowlist only with concrete ADR models and test unknown types
  fail closed.
- **Separate rubber_stamp and rubber_ducky releases may skew handoff versions**
  — reject unsupported `schema_version` values with setup guidance rather than
  coercing them.
