# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja #4 — wrap lifecycle in a LangGraph workflow
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Wrap the reducer in a two-node LangGraph. Initialization creates canonical
domain state; the event node pauses with a typed expectation, resumes with a
participant event, and calls the shared `apply_event` dispatcher. Invalid events
produce another interrupt containing the reducer error, allowing correction
without poisoning the checkpoint. Conditional routing either pauses for the
next participant or ends at a terminal domain status. The default in-memory
checkpointer uses an explicit strict serializer allowlist.

### Relevant Code / Diff
New file `src/agent_review/workflow.py`:

```python
"""LangGraph orchestration around the deterministic review reducer."""

from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from agent_review.lifecycle import (
    InvalidTransition,
    ReviewState,
    ReviewStatus,
    apply_event,
    start_review,
)
from agent_review.models import (
    BlockingConcernResponse,
    Concern,
    ConcernKind,
    Disposition,
    EscalationConcern,
    EscalationSummary,
    Position,
    PriorPointResponse,
    Rebuttal,
    RebuttalPoint,
    RebuttalRequest,
    ReviewRequest,
    ReviewResponse,
    Verdict,
)


class ReviewWorkflowState(TypedDict, total=False):
    """Checkpointed graph state for one review conversation."""

    request: ReviewRequest
    review: ReviewState


_TERMINAL_STATUSES = {
    ReviewStatus.APPROVED,
    ReviewStatus.WITHDRAWN,
    ReviewStatus.ESCALATED,
}

_EXPECTED_EVENT_TYPES = {
    ReviewStatus.AWAITING_REVIEW_RESPONSE: "review_response",
    ReviewStatus.AWAITING_REBUTTAL: "rebuttal",
    ReviewStatus.AWAITING_FINAL_POSITION: "rebuttal",
    ReviewStatus.AWAITING_ESCALATION_SUMMARY: "escalation_summary",
}

_CHECKPOINT_TYPES = (
    ReviewRequest,
    ReviewResponse,
    Rebuttal,
    EscalationSummary,
    Concern,
    PriorPointResponse,
    BlockingConcernResponse,
    RebuttalPoint,
    EscalationConcern,
    Position,
    Verdict,
    ConcernKind,
    Disposition,
    RebuttalRequest,
    ReviewState,
    ReviewStatus,
)


def _initialize(state: ReviewWorkflowState) -> ReviewWorkflowState:
    return {"review": start_review(state["request"])}


def _wait_for_event(state: ReviewWorkflowState) -> ReviewWorkflowState:
    review = state["review"]
    expected_round = (
        len(review.responses) + 1
        if review.status is ReviewStatus.AWAITING_REVIEW_RESPONSE
        else len(review.responses)
    )
    payload = {
        "status": review.status.value,
        "round": expected_round,
        "event_type": _EXPECTED_EVENT_TYPES[review.status],
    }
    validation_error: str | None = None
    while True:
        event = interrupt(
            payload
            if validation_error is None
            else {
                **payload,
                "error": validation_error,
            }
        )
        try:
            return {"review": apply_event(review, event)}
        except InvalidTransition as error:
            validation_error = str(error)


def _route_after_event(state: ReviewWorkflowState) -> Literal["wait", "end"]:
    return "end" if state["review"].status in _TERMINAL_STATUSES else "wait"


def build_review_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Compile the review workflow with an in-memory saver by default."""

    workflow = StateGraph(ReviewWorkflowState)
    workflow.add_node("initialize", _initialize)
    workflow.add_node("wait_for_event", _wait_for_event)
    workflow.add_edge(START, "initialize")
    workflow.add_edge("initialize", "wait_for_event")
    workflow.add_conditional_edges(
        "wait_for_event",
        _route_after_event,
        {
            "wait": "wait_for_event",
            "end": END,
        },
    )
    if checkpointer is None:
        checkpointer = InMemorySaver(
            serde=JsonPlusSerializer(allowed_msgpack_modules=_CHECKPOINT_TYPES)
        )
    return workflow.compile(
        checkpointer=checkpointer,
        name="agent-review",
    )
```

Lifecycle dispatcher refactor:

```diff
+def apply_event(state: ReviewState, event: ReviewEvent) -> ReviewState:
+    """Dispatch one validated protocol event to its domain transition."""
+
+    if isinstance(event, ReviewResponse):
+        return apply_review_response(state, event)
+    if isinstance(event, Rebuttal):
+        return apply_rebuttal(state, event)
+    if isinstance(event, EscalationSummary):
+        return finalize_escalation(state, event)
+    raise InvalidTransition(f"unsupported review event type: {type(event).__name__}")
@@
     for index, event in enumerate(events, start=1):
         try:
-            # Inline event-type dispatch
+            state = apply_event(state, event)
```

Dependency change:

```diff
 dependencies = [
+    "langgraph>=1.2.11",
     "pydantic>=2.13.4",
 ]
```

The six complete behavior tests are in `tests/test_workflow.py`. They cover
initial interruption, direct approval, multi-round reviewer/worker resumes,
escalation-summary resume, invalid-event error reprompt and correction, and
in-memory thread isolation. Valid events use `Command(resume=...)` directly.
The full suite runs under `LANGGRAPH_STRICT_MSGPACK=true`.

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ LANGGRAPH_STRICT_MSGPACK=true uv run pytest -q
................................................................         [100%]
64 passed in 0.16s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### Known Concerns
1. The explicit checkpoint type allowlist must be updated when new domain model
   classes enter graph state.
2. Callers supplying a custom checkpointer own its serializer configuration.
3. Checkpoints are in-memory only; process restart remains the durable
   persistence slice.

### Specific Questions for Reviewer
1. Does the graph remain a thin orchestration layer over the reducer?
2. Is the interrupt retry loop safe and consistent with LangGraph resume
   semantics?
3. Does strict checkpoint serialization cover every current domain type without
   over-broad module trust?

## Review Response — Round 1
**Position:** AGREE

Verification performed before verdict (protocol v1.3): `ruff check src tests` — all checks passed; `LANGGRAPH_STRICT_MSGPACK=true pytest -q` — 64 passed. I inspected `src/agent_review/workflow.py`, the `apply_event` dispatcher in `src/agent_review/lifecycle.py`, `tests/test_workflow.py`, `pyproject.toml`, `uv.lock` (langgraph pinned at 1.2.11), and the installed serializer source.

Answers to the specific questions:

1. Yes. The graph is genuinely thin: `_initialize` calls `start_review`, `_wait_for_event` calls `apply_event`, and `_route_after_event` keys only off domain status. No protocol rule is duplicated in the graph; every validation path lives in the reducer.
2. Yes. The `while True` / `interrupt` loop is LangGraph's documented validation pattern: resume values are checkpointed per task and replayed by interrupt call index, so after an invalid event the node deterministically re-raises the same `InvalidTransition` on replay (the reducer is pure) and consumes the next resume at the second interrupt. The `review` channel is never written with a bad event, so the checkpoint is not poisoned. `tests/test_workflow.py::test_invalid_resume_reprompts_with_reducer_error` exercises exactly this.
3. Yes. In the locked `langgraph-checkpoint` 4.2.0, `_normalize_module_keys` (`.venv/.../langgraph/checkpoint/serde/jsonplus.py`, lines 874-883) converts each class in `allowed_msgpack_modules` to an exact `(module, name)` pair — trust is per class, not per module. All sixteen types reachable through graph channels or resume payloads (three event models, their five nested models, six enums, `ReviewRequest`, `ReviewState`) are listed in `_CHECKPOINT_TYPES`, and the strict-mode test run confirms nothing else is required.

### Blocking Concerns
None.

### Non-Blocking Suggestions
S1. `workflow.py` line 11 imports `typing_extensions` directly, but `typing-extensions` is not declared in `[project.dependencies]` — the import currently works only via pydantic's transitive dependency. Since `requires-python = ">=3.12"`, `typing.TypedDict` is sufficient here (LangGraph needs `typing_extensions.TypedDict` only on Python < 3.12); either switch the import or declare the dependency so install correctness does not ride on a transitive pin.

S2. No graph-level test exercises the `AWAITING_FINAL_POSITION` interrupt (round-3 REVISE → FINAL-POSITION rebuttal → escalation summary). The reducer path is covered in `tests/test_lifecycle.py` (lines 511 and 580), but this is the one status where `_EXPECTED_EVENT_TYPES` maps a rebuttal expectation onto the `len(review.responses)` round arithmetic, and its interrupt payload is never asserted through the graph. A single test would close the gap.

S3. The interrupt payload reports `"round": len(review.responses)` for `AWAITING_ESCALATION_SUMMARY`, but escalation summaries are not round-scoped in protocol v1.3. Harmless, though a consumer could misread it as a constraint; consider omitting `round` for that status or documenting it as the last completed response round.

**Verdict:** APPROVE

## Post-Approval Follow-Up
All three suggestions were adopted:

- S1: switched to Python 3.12's standard-library `TypedDict`.
- S2: added a full graph test through round-3 REVISE, FINAL-POSITION, and the
  escalation-summary interrupt.
- S3: escalation-summary interrupt payloads no longer report a round.

Ruff passes, strict-msgpack mode passes all 65 tests, and the package builds
successfully.
