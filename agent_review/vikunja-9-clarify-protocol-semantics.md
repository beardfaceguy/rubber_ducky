# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja #9 — clarify blocking protocol semantics
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Narrow the protocol to code and diff reviews, define a maximum of three reviewer
responses, allocate concern IDs monotonically by role, and require the reviewer
to verify accepted changes before consensus. A rebuttal now carries the actual
revised diff so re-review is evidence-based.

### Relevant Code / Diff
```diff
--- a/skills/agent-review/SKILL.md
+++ b/skills/agent-review/SKILL.md
@@
-Use this skill for formal agent-to-agent reviews of plans, code, or diffs.
+Use this skill for formal agent-to-agent reviews of code or diffs. Conceptual
+and high-level plan reviews are deferred to a future protocol version.

--- a/skills/agent-review/references/review-protocol.md
+++ b/skills/agent-review/references/review-protocol.md
@@
-**Version:** 1.2
-**Purpose:** Standard format for agent-to-agent solution reviews. Both the worker
+**Version:** 1.3
+**Purpose:** Standard format for agent-to-agent code and diff reviews. Both the
 worker agent (requesting review) and the reviewer agent MUST follow this protocol exactly.
@@
 **Changelog:**
+- v1.3 — Defined exact round counting and ID allocation, and required reviewer
+  verification of accepted revisions before consensus.
@@
 1. The worker submits a **Review Request** for a single task.
 2. The reviewer responds with a **Review Response**.
-3. The worker either accepts the verdict or responds with a **Rebuttal**.
-4. The exchange continues until consensus is reached or each party has
-   responded **3 times**, whichever comes first.
-5. If no consensus after 3 rounds each, the worker records the deadlock and
-   **escalates to the human operator**. Do not continue past round 3.
+3. If the verdict is REVISE, the worker responds with a **Rebuttal** that
+   addresses every blocking concern and includes the revised diff when it
+   accepts any concern.
+4. The reviewer inspects the rebuttal and revised diff, then sends the next
+   numbered **Review Response**.
+5. The exchange continues until consensus or the end of round 3.
+6. If the round-3 response is not APPROVE, the worker may append a round-3
+   rebuttal to record its final position, then records the deadlock and
+   **escalates to the human operator**. The reviewer must not send a round-4
+   response.
+
+A review has at most three numbered reviewer responses:
+
+- Round 1 starts with the initial Review Request and ends with Review Response
+  1 plus an optional Rebuttal 1.
+- Rounds 2 and 3 each start with the Review Response to the preceding rebuttal
+  and end with the same-numbered optional rebuttal.
+- The initial Review Request is not counted as a worker response. A worker may
+  send at most one rebuttal for each numbered reviewer response.
@@
-**Protocol:** review-protocol.md v1.2 — respond using the Review Response format.
+**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.
@@
 ### Responses to Blocking Concerns
 <Address each blocking concern BY NUMBER (e.g., "Re B1: ..."). For each, state
 one of: ACCEPT (will fix), DISPUTE (with counter-argument and evidence), or
 CLARIFY (concern is based on a misreading — explain).>

+### Revised Code / Diff
+<Required in every rebuttal. If any blocking concern is ACCEPTed, implement it
+and include the actual revised code or diff. If the rebuttal only disputes or
+clarifies concerns and no code changed, state "Unchanged — see Review Request.">
+
 ### New Points
@@
 - Every concern, suggestion, and rebuttal point gets a stable ID (B1, S2, R3).
   All later references use the ID. Never re-raise a concern under new wording
   or reissue an existing concern under a new ID — reference the original.
-- A blocking concern is resolved only when the party that raised it explicitly
-  says "B<n>: resolved" (or it is ACCEPTed by the worker).
+- The reviewer allocates B and S IDs; the worker allocates R IDs. Each prefix
+  has its own conversation-wide, monotonically increasing sequence starting at
+  1. IDs are never reused or renumbered between rounds.
+- ACCEPT acknowledges that a blocking concern is valid but does not resolve
+  it. A blocking concern is resolved only when the reviewer explicitly says
+  "B<n>: resolved" after inspecting the revised code or diff.
@@
 ### Consensus
-- Consensus = a Review Response with verdict **APPROVE**, or the worker
-  ACCEPTing all blocking concerns and stating a revision plan.
+- Consensus = a Review Response with verdict **APPROVE**.
+- A revision plan or an ACCEPT response is not consensus. Accepted changes
+  must be implemented, included in the rebuttal, and verified by the reviewer.
 - Only blocking concerns can prevent APPROVE. Do not deadlock over
   non-blocking suggestions.
@@
 ### Escalation report
-When 3 rounds each pass without consensus (or a party issues ESCALATE), the
-worker appends to the log:
+When the round-3 response does not reach consensus (or a party issues
+ESCALATE), the worker appends to the log:
@@
 - One task per review conversation. Do not bundle tasks.
 - Reviews are of the actual code/diff, not descriptions of it.
+- Conceptual and high-level plan reviews are not supported by this version of
+  the protocol.
```

### Known Concerns
1. The revised sequence treats the initial request as the start of round 1 but
   does not count it as a worker response.
2. A rebuttal must include the revised diff directly rather than introducing a
   separate revised-request message type.

### Specific Questions for Reviewer
1. Is the three-round sequence deterministic and internally consistent?
2. Can accepted concerns reach consensus only after evidence-based verification?
3. Does this remain narrowly scoped to formal code and diff review?

## Review Response — Round 1
**Position:** AGREE

### Blocking Concerns
None.

I verified the diff against the actual files rather than the description: every hunk is faithfully applied in `/home/beardface/.agents/skills/agent-review/SKILL.md` (lines 9–10, scope narrowed to code/diff reviews) and `references/review-protocol.md` (version/purpose lines 3–4, changelog lines 10–11, overview steps 3–6 and round-counting block lines 43–56, template version line 81, Rebuttal "Revised Code / Diff" section lines 129–132, ID-allocation and ACCEPT-resolution rules lines 148–153, consensus lines 164–167, escalation trigger lines 187–188, scope note lines 206–207). On the worker's specific questions: (1) yes — the sequence is deterministic: at most three numbered reviewer responses, each round boundary is defined, and the round-3 terminal behavior is explicit; (2) yes — ACCEPT no longer resolves anything, resolution requires the reviewer's explicit "B\<n\>: resolved" after inspecting the revised diff, and consensus is only a Review Response with verdict APPROVE; (3) yes — plan reviews are excluded consistently in both SKILL.md and the Scope Notes. Both Known Concerns describe text that is present and internally consistent as applied.

### Non-Blocking Suggestions
S1. The Review Request template still reads `## Review Request — Round N` (review-protocol.md line 79), but under the new round definition a Review Request can only ever occur in round 1 — rounds 2 and 3 start with the Review Response. The vestigial "Round N" could invite a worker to send a "Review Request — Round 2" that the sequence no longer permits. Suggest hardcoding "Round 1" or noting the constraint.

S2. The Rebuttal footer requires `**Requesting:** RE-REVIEW | WITHDRAWN` (line 137), but the optional round-3 rebuttal exists only "to record its final position" and by rule can receive no response ("The reviewer must not send a round-4 response"). A round-3 rebuttal requesting RE-REVIEW is unanswerable. Consider a third value (e.g., FINAL-POSITION) or an explicit note that RE-REVIEW is invalid in round 3.

S3. The resolution rule (lines 151–153) says a blocking concern is resolved "only when the reviewer explicitly says 'B\<n\>: resolved' after inspecting the revised code or diff." When a concern is closed via CLARIFY or a successful DISPUTE, no revised code exists; the "Unchanged — see Review Request" case covers this in practice, but the wording would be tighter as "after inspecting the rebuttal and any revised code or diff."

**Verdict:** APPROVE
