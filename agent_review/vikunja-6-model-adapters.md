# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja #6 — add worker and reviewer model adapters
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Add provider-neutral adapters over LangChain's `with_structured_output`
contract. The reviewer adapter can only request a `ReviewResponse`; the worker
selects `Rebuttal` or `EscalationSummary` from lifecycle status. Both revalidate
raw output with Pydantic and dry-run the event through the reducer. Neither
adapter binds tools. Rebuttals cannot ACCEPT or introduce code unless the caller
supplies an authoritative revised diff, which the model must echo exactly.

### Relevant Code / Diff
New file `src/agent_review/adapters.py`:

```python
"""Provider-neutral LangChain adapters for review participants."""

from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from agent_review.lifecycle import (
    InvalidTransition,
    ReviewState,
    ReviewStatus,
    apply_event,
)
from agent_review.models import Disposition, EscalationSummary, Rebuttal, ReviewResponse

_UNCHANGED_DIFF = "Unchanged — see Review Request."


class StructuredOutputModel(Protocol):
    """Minimal LangChain model contract required by participant adapters."""

    def with_structured_output(
        self,
        schema: type[BaseModel],
        **kwargs: Any,
    ) -> Runnable[Any, Any]: ...


def _participant_messages(
    role: str,
    state: ReviewState,
    evidence_instruction: str = "",
) -> list[BaseMessage]:
    return [
        SystemMessage(
            content=(
                f"You are the {role} in review-protocol.md v1.3. "
                "Return only the requested structured protocol message. "
                "You have no tools and must not perform external writes."
            )
        ),
        HumanMessage(
            content=(
                "Produce the next protocol event for this canonical review state:\n"
                f"{state.model_dump_json(indent=2)}"
                f"{evidence_instruction}"
            )
        ),
    ]


@dataclass(frozen=True)
class ReviewerAdapter:
    """Generate validated reviewer responses without binding tools."""

    model: StructuredOutputModel

    def review(self, state: ReviewState) -> ReviewResponse:
        if state.status is not ReviewStatus.AWAITING_REVIEW_RESPONSE:
            raise InvalidTransition(
                f"reviewer cannot act while status is {state.status.value}"
            )
        runnable = self.model.with_structured_output(ReviewResponse)
        raw_response = runnable.invoke(_participant_messages("reviewer", state))
        response = ReviewResponse.model_validate(raw_response)
        apply_event(state, response)
        return response


@dataclass(frozen=True)
class WorkerAdapter:
    """Generate validated rebuttals or escalation summaries without tools."""

    model: StructuredOutputModel

    def respond(
        self,
        state: ReviewState,
        *,
        revised_diff: str | None = None,
    ) -> Rebuttal | EscalationSummary:
        if state.status in {
            ReviewStatus.AWAITING_REBUTTAL,
            ReviewStatus.AWAITING_FINAL_POSITION,
        }:
            schema: type[Rebuttal | EscalationSummary] = Rebuttal
        elif state.status is ReviewStatus.AWAITING_ESCALATION_SUMMARY:
            schema = EscalationSummary
        else:
            raise InvalidTransition(
                f"worker cannot act while status is {state.status.value}"
            )

        evidence_instruction = ""
        if schema is Rebuttal:
            evidence_instruction = (
                "\n\nNo revised diff was supplied by the caller. Do not ACCEPT concerns "
                f"or invent code; use exactly {_UNCHANGED_DIFF!r}."
                if revised_diff is None
                else (
                    "\n\nUse this caller-supplied revised diff exactly; do not alter or "
                    f"invent code:\n{revised_diff}"
                )
            )
        runnable = self.model.with_structured_output(schema)
        raw_response = runnable.invoke(
            _participant_messages("worker", state, evidence_instruction)
        )
        event = schema.model_validate(raw_response)
        if isinstance(event, Rebuttal):
            accepted = any(
                response.disposition is Disposition.ACCEPT
                for response in event.blocking_responses
            )
            if revised_diff is None:
                if accepted:
                    raise InvalidTransition(
                        "ACCEPT requires a caller-supplied revised diff"
                    )
                if event.revised_diff != _UNCHANGED_DIFF:
                    raise InvalidTransition("worker adapter cannot invent revised code")
            elif event.revised_diff != revised_diff.strip():
                raise InvalidTransition(
                    "model output does not match caller-supplied revised diff"
                )
        apply_event(state, event)
        return event
```

Dependency change:

```diff
 dependencies = [
+    "langchain-core>=1.6.0",
     "langgraph>=1.2.11",
```

The ten complete tests in `tests/test_adapters.py` use a fake
`with_structured_output` model and cover reviewer validation, lifecycle dry-run,
malformed output, pre-call status rejection, rebuttal and escalation schema
selection, absence of tool binding, caller-supplied evidence, invented-diff
rejection, and exact-diff enforcement.

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ LANGGRAPH_STRICT_MSGPACK=true uv run pytest -q
........................................................................ [ 90%]
........                                                                 [100%]
80 passed in 0.19s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### Known Concerns
1. Adapters make one model attempt; retry and backoff remain application-service
   policy.
2. The injected model is assumed not to have been pre-bound to tools; the
   adapters themselves never call `bind_tools`.
3. Caller-supplied diffs are normalized by the existing Pydantic field's
   surrounding-whitespace stripping before exact comparison.

### Specific Questions for Reviewer
1. Are reviewer and worker capability boundaries correctly enforced?
2. Can model output bypass Pydantic or lifecycle validation?
3. Does the authoritative-diff boundary prevent model-fabricated code from
   entering a rebuttal?

## Review Response — Round 1
**Position:** AGREE

Verification performed against the actual tree, not the request text: `src/agent_review/adapters.py` matches the submitted diff exactly; `pyproject.toml` adds `langchain-core>=1.6.0` and `uv.lock` pins 1.6.0; `uvx ruff check src tests` is clean; the full suite passes (`80 passed in 0.18s`, of which 10 are in `tests/test_adapters.py`, matching the claim).

Answers to the specific questions:

1. Yes. The reviewer adapter can only request the `ReviewResponse` schema and rejects any status other than `AWAITING_REVIEW_RESPONSE` before the model is invoked (`adapters.py:61-65`); the worker adapter derives `Rebuttal` vs `EscalationSummary` solely from lifecycle status and rejects everything else (`adapters.py:84-94`). Neither path references `bind_tools`, and the tests assert the fake's `bind_tools` is never called and that rejection happens before any model input is sent.
2. Effectively no, with one caveat recorded as S1: `model_validate` does not re-run validators on an already-constructed instance of the same class (Pydantic v2 `revalidate_instances` defaults to `"never"`), so the "revalidate raw output" guarantee holds unconditionally only for dict payloads. In the normal LangChain path instances are validated at construction and `ProtocolModel` is frozen, so bypass requires a broken integration returning a `model_construct()` instance. The lifecycle dry-run via `apply_event` independently enforces round numbering, ID sequencing, concern coverage, and status legality regardless.
3. Yes, for the diff channel. With no caller diff, any ACCEPT raises and any non-sentinel `revised_diff` raises (`adapters.py:117-123`); with a caller diff, only an exact whitespace-stripped echo passes (`adapters.py:124-127`), consistent with the `NonEmptyText` stripping noted in Known Concern 3. Fabricated code can still appear in free-text fields (`reason`, `new_points`), but those are prose fields and never become the authoritative diff.

### Blocking Concerns
None.

### Non-Blocking Suggestions
S1: Set `revalidate_instances="always"` on `ProtocolModel` (`models.py:18-21`), or validate `model_dump()` of the raw output, so the revalidation claim in the adapters holds even for pre-constructed same-class instances returned by a provider integration.

S2: `_UNCHANGED_DIFF` (`adapters.py:18`) duplicates the string literal hardcoded in the `Rebuttal` validator (`models.py:118`). If either changes, the adapter guard and the model-level validator silently diverge. Define the sentinel once (e.g., export it from `models`) and import it in both places.

S3: Two adapter branches are untested: the invented-diff rejection when no concern is ACCEPTed (`adapters.py:122-123` — model disputes everything yet emits a non-sentinel diff), and `Rebuttal` schema selection under `AWAITING_FINAL_POSITION` (`adapters.py:84-88`). Both are one-test additions using the existing fake.

S4: `WorkerAdapter.respond` silently ignores a caller-supplied `revised_diff` when status selects `EscalationSummary`. Raising on that combination would surface a caller bug instead of discarding the argument.

**Verdict:** APPROVE

## Post-Approval Follow-Up
All four suggestions were adopted:

- S1: all protocol models now revalidate preconstructed instances.
- S2: `UNCHANGED_DIFF` is defined once in `models` and reused by adapters,
  validation, and audit logging.
- S3: added tests for dispute-time invented diffs and final-position schema
  selection.
- S4: escalation-summary generation rejects irrelevant caller diffs before the
  model is invoked.

Additional tests pin exact authoritative-diff echoing and malformed
preconstructed output. Ruff passes, 84 tests pass under strict checkpoint mode,
and the package builds successfully.
