# Agent Review Protocol

**Version:** 1.3
**Purpose:** Standard format for agent-to-agent reviews. Both the worker agent
(requesting review) and the reviewer agent MUST follow this protocol exactly.
Deviation from this format is itself a protocol violation and should be called
out by the other party.

This protocol is domain-agnostic: it governs roles, rounds, ID allocation,
verdicts, consensus, and escalation. The **reviewed artifact** — a code diff, a
plan document, etc. — and its exact section headings are defined by the review
domain's own skill (`rubber_ducky_code`, `rubber_ducky_plan`). Everything else
below is identical across domains.

**Changelog:**

- v1.3 — Defined exact round counting, terminal message values, and ID
  allocation, and required reviewer verification before consensus. Generalized
  the reviewed artifact so code and plan domains share one protocol.
- v1.2 — Log location is now workspace-relative (`agent_review/` at workspace
  root, created if missing) instead of a fixed absolute path.
- v1.1 — Added Role Determination and Missing Information sections.
- v1.0 — Initial version.

---

## Role Determination

Identify which role you are performing before doing anything else:

- **Worker:** You are executing a task and initiating review. You request
  reviews, own and update the review log, evaluate concerns, submit rebuttals,
  and produce escalation summaries.
- **Reviewer:** Your prompt contains a Review Request addressed to you. You
  evaluate the actual reviewed artifact, return protocol-formatted responses,
  track concerns by stable ID, and NEVER write files.

If your role is ambiguous, ask the operator to clarify before proceeding.

---

## Overview

1. The worker submits a **Review Request** for a single task.
2. The reviewer responds with a **Review Response**.
3. If the verdict is REVISE, the worker responds with a **Rebuttal** that
   addresses every blocking concern and includes the revised artifact when it
   accepts any concern.
4. The reviewer inspects the rebuttal and revised artifact, then sends the next
   numbered **Review Response**.
5. The exchange continues until consensus or the end of round 3.
6. If the round-3 response is not APPROVE, the worker may append a round-3
   rebuttal requesting FINAL-POSITION to record its final position, then
   records the deadlock and **escalates to the human operator**. The reviewer
   must not send a round-4 response.

A review has at most three numbered reviewer responses:

- Round 1 starts with the initial Review Request and ends with Review Response
  1 plus an optional Rebuttal 1.
- Rounds 2 and 3 each start with the Review Response to the preceding rebuttal
  and end with the same-numbered optional rebuttal.
- The initial Review Request is not counted as a worker response. A worker may
  send at most one rebuttal for each numbered reviewer response.

The worker owns the log file. The reviewer never writes files.

---

## Log File

- Location: `agent_review/` at the root of the current workspace (the
  repository or project directory the worker is operating in). If the
  directory does not exist, the worker creates it before writing the first
  log entry.
- Naming: `<task-id>-<short-slug>.md` (e.g., `DAI-42-transport-trait-seam.md`)
- The log begins with the protocol version in use, then records every message
  from both parties, in order, verbatim, using the formats below.

---

## Message Formats

The **Reviewed Artifact** and **Revised Artifact** sections carry the domain
payload. The domain skill names their exact headings (e.g. "Relevant Code /
Diff" and "Revised Code / Diff"; "Proposed Plan" and "Revised Plan") and their
contents. Never summarize the artifact — include it verbatim.

### 1. Review Request (worker → reviewer)

```markdown
## Review Request — Round 1
**Task:** <task-id and one-line description>
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
<Concise description of the approach and reasoning.>

### Reviewed Artifact
<The full artifact under review, under the heading your domain skill defines.
Never summarize — include it.>

### Known Concerns
<Numbered list of the worker's own doubts or open questions, or "None.">

### Specific Questions for Reviewer
<Numbered list, or "General review requested.">
```

### 2. Review Response (reviewer → worker)

```markdown
## Review Response — Round N
**Position:** AGREE | DISAGREE | PARTIAL

### Blocking Concerns
<Numbered list: B1, B2, ... Each concern must state WHAT is wrong and WHY it
matters. If none: "None.">

### Non-Blocking Suggestions
<Numbered list: S1, S2, ... Style, optimization, or optional improvements.
If none: "None.">

### Responses to Prior Points
<Only in rounds 2+. Address each of the worker's rebuttal points BY NUMBER
(e.g., "Re R1: ..."). Do not paraphrase or skip any. Explicitly mark previously
raised blocking concerns as resolved when appropriate (e.g., "B2: resolved").>

**Verdict:** APPROVE | REVISE | ESCALATE
```

### 3. Rebuttal (worker → reviewer)

```markdown
## Rebuttal — Round N
**Position:** AGREE | DISAGREE | PARTIAL

### Responses to Blocking Concerns
<Address each blocking concern BY NUMBER (e.g., "Re B1: ..."). For each, state
one of: ACCEPT (will fix), DISPUTE (with counter-argument and evidence), or
CLARIFY (concern is based on a misreading — explain).>

### Revised Artifact
<Required in every rebuttal, under the heading your domain skill defines. If any
blocking concern is ACCEPTed, apply it and include the actual revised artifact.
If the rebuttal only disputes or clarifies concerns and nothing changed, state
"Unchanged — see Review Request.">

### New Points
<Numbered list: R1, R2, ... New arguments or evidence, or "None.">

**Requesting:** RE-REVIEW | WITHDRAWN | FINAL-POSITION
<RE-REVIEW is valid only in rounds 1-2. FINAL-POSITION is valid only in round
3. WITHDRAWN is valid in any round.>
```

---

## Rules

### Concern tracking
- Every concern, suggestion, and rebuttal point gets a stable ID (B1, S2, R3).
  All later references use the ID. Never re-raise a concern under new wording
  or reissue an existing concern under a new ID — reference the original.
- The reviewer allocates B and S IDs; the worker allocates R IDs. Each prefix
  has its own conversation-wide, monotonically increasing sequence starting at
  1. IDs are never reused or renumbered between rounds.
- ACCEPT acknowledges that a blocking concern is valid but does not resolve
  it. A blocking concern is resolved only when the reviewer explicitly says
  "B<n>: resolved" after inspecting the rebuttal and any revised artifact.

### Verdicts
- **APPROVE:** No unresolved blocking concerns. Non-blocking suggestions may
  remain open; the worker records them in the log and proceeds.
- **REVISE:** At least one unresolved blocking concern. Worker must rebut or
  accept each one.
- **ESCALATE:** The reviewer believes the disagreement cannot be resolved
  between agents (use sparingly; normally rounds run their course).

### Consensus
- Consensus = a Review Response with verdict **APPROVE**.
- A revision plan or an ACCEPT response is not consensus. Accepted changes
  must be applied, included in the rebuttal, and verified by the reviewer.
- Only blocking concerns can prevent APPROVE. Do not deadlock over
  non-blocking suggestions.

### Honest disagreement
- The reviewer's word is not law. The worker MUST evaluate each concern on its
  merits and DISPUTE with a concrete counter-argument when it disagrees.
- Neither party may capitulate merely to end the exchange. Agreement must be
  stated with a reason ("Re B2: ACCEPT — the race you describe is real because
  ...", not "ACCEPT.").
- Cite evidence where possible: file paths, line numbers, doc links, test
  output, language/library semantics, prior decisions, dependencies.

### Missing information
- Do not fabricate task identifiers, round numbers, artifact contents, prior
  messages, test results, or review-log contents.
- If required information is missing from your context, request it from the
  other party (or the operator) before issuing a verdict, rebuttal, or log
  entry that depends on it.

### Escalation report
When the round-3 response does not reach consensus (or a party issues
ESCALATE), the worker appends to the log:

```markdown
## Escalation Summary
**Unresolved blocking concerns:** <IDs with one-line status each>
**Worker's final position:** <2-3 sentences>
**Reviewer's final position:** <2-3 sentences>
**Decision needed from operator:** <the specific question(s) to decide>
```

...then stops work on the task and surfaces the summary to the human operator.

---

## Scope Notes

- One task per review conversation. Do not bundle tasks.
- Reviews are of the actual reviewed artifact, not descriptions of it.
- Each domain skill defines the artifact's format and section headings; do not
  invent a payload shape the skill does not specify.
- Trivial changes may skip review entirely; the worker notes "review skipped:
  trivial" in the task log.
