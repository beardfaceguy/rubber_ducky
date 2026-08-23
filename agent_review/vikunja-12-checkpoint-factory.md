# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja #12 — centralize checkpoint serializer factory
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Move checkpoint trust policy into a dedicated module. The serializer factory
allows every current review-domain type by default and accepts explicit trusted
extension types. The in-memory saver factory and workflow both consume that
single serializer factory. Broad module discovery is deliberately avoided
because it would defeat the security boundary.

### Relevant Code / Diff
New file `src/agent_review/checkpointing.py`:

```python
"""Checkpoint factories with explicit, extensible type trust."""

from collections.abc import Iterable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from agent_review.lifecycle import ReviewState, ReviewStatus
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


_REVIEW_CHECKPOINT_TYPES: tuple[type, ...] = (
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


def review_checkpoint_serializer(
    additional_types: Iterable[type] = (),
) -> JsonPlusSerializer:
    """Create a strict serializer for review state plus trusted extensions."""

    allowed_types = tuple(dict.fromkeys((*_REVIEW_CHECKPOINT_TYPES, *additional_types)))
    return JsonPlusSerializer(allowed_msgpack_modules=allowed_types)


def in_memory_review_checkpointer(
    additional_types: Iterable[type] = (),
) -> InMemorySaver:
    """Create an in-memory saver using the shared review serializer."""

    return InMemorySaver(serde=review_checkpoint_serializer(additional_types))
```

Workflow refactor:

```diff
-from langgraph.checkpoint.memory import InMemorySaver
-from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
+from agent_review.checkpointing import in_memory_review_checkpointer
@@
-_CHECKPOINT_TYPES = (...)
@@
     if checkpointer is None:
-        checkpointer = InMemorySaver(
-            serde=JsonPlusSerializer(allowed_msgpack_modules=_CHECKPOINT_TYPES)
-        )
+        checkpointer = in_memory_review_checkpointer()
```

New tests:

```python
class ExtensionState(BaseModel):
    value: str


def test_serializer_factory_accepts_explicit_extension_types() -> None:
    serializer = review_checkpoint_serializer(additional_types=(ExtensionState,))
    original = ExtensionState(value="extension")

    restored = serializer.loads_typed(serializer.dumps_typed(original))

    assert restored == original


def test_serializer_factory_covers_default_review_state() -> None:
    serializer = review_checkpoint_serializer()
    original = start_review(
        ReviewRequest(
            task_id="AR-4",
            title="Checkpoint review",
            proposed_solution="Use one serializer factory.",
            relevant_diff="+serializer = shared",
        )
    )

    restored = serializer.loads_typed(serializer.dumps_typed(original))

    assert restored == original
```

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ LANGGRAPH_STRICT_MSGPACK=true uv run pytest -q
...................................................................      [100%]
67 passed in 0.18s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### Known Concerns
1. New domain classes still require deliberate registration; the factory
   centralizes that maintenance rather than guessing what is trustworthy.
2. Third-party checkpointer constructors vary, so callers pass the shared
   serializer into those constructors rather than having instances mutated.

### Specific Questions for Reviewer
1. Does the factory provide sufficient extension without over-broad trust?
2. Is type deduplication deterministic and safe for generators?
3. Does the workflow now have one authoritative serializer configuration path?

## Review Response — Round 1
**Position:** PARTIAL

### Blocking Concerns
1. **B1 — The security boundary this module exists to enforce has no test.** `tests/test_checkpointing.py` contains only positive round-trips: allowed types restore correctly (lines 12–34). Nothing asserts that an *unregistered* type is rejected. I verified the boundary manually: with the default factory, an unregistered pydantic model degrades to its raw kwargs dict (`{'value': 'attack'}`) with the constructor never invoked, and this holds independently of `LANGGRAPH_STRICT_MSGPACK` because the factory always passes an explicit allowlist (`jsonplus.py` lines 107–119 and 645–649 in the installed langgraph). So the boundary works today — but a regression *in this module* (dropping the `allowed_msgpack_modules` argument, or defaulting it to permissive `True`) would leave all 67 tests green while silently re-opening arbitrary-constructor deserialization. The request's own rationale ("broad module discovery … would defeat the security boundary") makes this property the point of the change; it must be pinned. Fix is one test: serialize an unregistered `BaseModel` through `review_checkpoint_serializer()` and assert the restored value is not an instance of that type. (This is also the qualified answer to Q1: the trust design is sound — explicit types only, normalized to `(module, name)` pairs, unregistered types blocked — but the property is unverified by the suite. Q3 is confirmed unqualified: `src/agent_review/checkpointing.py` is the only place in `src` constructing `JsonPlusSerializer` or `InMemorySaver`; `workflow.py` lines 10 and 97–98 consume only `in_memory_review_checkpointer`, and no `_CHECKPOINT_TYPES` remnant survives.)

### Non-Blocking Suggestions
1. **S1 — Add a registration drift guard for Known Concern 1.** Forgetting to register a future domain class fails silently at a distance: checkpoint restore returns plain dicts with only a logged warning, and downstream code breaks far from the cause. A test asserting that every public `BaseModel` and `StrEnum` in `agent_review.models` and `agent_review.lifecycle` appears in `_REVIEW_CHECKPOINT_TYPES` converts forgot-to-register into an immediate failure. Related observation, no change requested: the five nested models (`Concern`, `PriorPointResponse`, `BlockingConcernResponse`, `RebuttalPoint`, `EscalationConcern`) never actually reach the msgpack extension path today, because `model_dump()` flattens nested models to dicts before encoding (`jsonplus.py` lines 308–319); their entries are redundant but harmless and defensively correct.
2. **S2 — The `dict.fromkeys` deduplication in `review_checkpoint_serializer` (checkpointing.py line 51) is redundant.** Answering Q2 directly: it is deterministic (insertion-ordered) and generator-safe (the splat consumes the iterable exactly once), so it is not *wrong* — but langgraph normalizes the allowlist to a `set` anyway (`_normalize_module_keys`, `jsonplus.py` lines 874–883; public contract `AllowedMsgpackModules = Iterable[tuple[str, ...] | type]` in `_msgpack.py` line 97). Passing the concatenated tuple straight through is simpler. Optional.

Verification performed on the actual tree: the code in the request matches `src/agent_review/checkpointing.py` and `src/agent_review/workflow.py` verbatim; `uvx ruff check src tests` passes; the full suite passes both with `LANGGRAPH_STRICT_MSGPACK=true` (67 passed) and without it (checkpointing + workflow subset, 9 passed), confirming the claimed results.

**Verdict:** REVISE

## Rebuttal — Round 1
**Position:** AGREE

### Responses to Blocking Concerns
Re B1: ACCEPT — the allowlist is a security boundary, so its rejection behavior
must be explicit. Added a regression test proving an unregistered Pydantic type
is not reconstructed by the default serializer.

### Revised Code / Diff
```diff
--- a/src/agent_review/checkpointing.py
+++ b/src/agent_review/checkpointing.py
@@
     Position,
     PriorPointResponse,
+    ProtocolModel,
@@
     ReviewState,
     ReviewStatus,
+    ProtocolModel,
 )


+def review_checkpoint_types(
+    additional_types: Iterable[type] = (),
+) -> tuple[type, ...]:
+    """Return the canonical trusted type set plus explicit extensions."""
+
+    return tuple(dict.fromkeys((*_REVIEW_CHECKPOINT_TYPES, *additional_types)))
+
+
 def review_checkpoint_serializer(
@@
-    allowed_types = tuple(dict.fromkeys((*_REVIEW_CHECKPOINT_TYPES, *additional_types)))
-    return JsonPlusSerializer(allowed_msgpack_modules=allowed_types)
+    return JsonPlusSerializer(
+        allowed_msgpack_modules=review_checkpoint_types(additional_types)
+    )

--- a/tests/test_checkpointing.py
+++ b/tests/test_checkpointing.py
@@
+class UnregisteredState(BaseModel):
+    value: str
+
+
+def test_serializer_factory_does_not_restore_unregistered_type() -> None:
+    serializer = review_checkpoint_serializer()
+    original = UnregisteredState(value="untrusted")
+
+    restored = serializer.loads_typed(serializer.dumps_typed(original))
+
+    assert not isinstance(restored, UnregisteredState)
+
+
+def test_every_domain_checkpoint_type_is_registered() -> None:
+    defined_domain_types = {
+        value
+        for module in (models_module, lifecycle_module)
+        for value in vars(module).values()
+        if isinstance(value, type)
+        and value.__module__ == module.__name__
+        and issubclass(value, (BaseModel, StrEnum))
+    }
+
+    assert defined_domain_types <= set(review_checkpoint_types())
```

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ LANGGRAPH_STRICT_MSGPACK=true uv run pytest -q
.....................................................................    [100%]
69 passed in 0.19s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### New Points
R1. Adopted S1 by exposing the canonical type tuple through
`review_checkpoint_types` and adding a reflection-based drift guard over every
domain `BaseModel` and `StrEnum`.

R2. Retained deterministic deduplication because
`review_checkpoint_types` is now a public utility whose canonical output may be
consumed outside LangGraph; the implementation remains one line and consumes
extension generators once.

**Requesting:** RE-REVIEW

## Review Response — Round 2
**Position:** AGREE

### Blocking Concerns
None.

### Non-Blocking Suggestions
1. **S3 — The B1 regression test's power depends on the environment it runs in.** I verified by simulation that both regression modes (dropping `allowed_msgpack_modules` entirely, and passing permissive `True`) reconstruct an unregistered `BaseModel` in the default environment, so `test_serializer_factory_does_not_restore_unregistered_type` fails and catches them — but only when the suite runs *without* `LANGGRAPH_STRICT_MSGPACK=true`. Under strict mode, a no-allowlist serializer rejects unregistered types anyway, so that regression would be masked in a strict-only run. The verification commands recorded in this log use strict mode as the canonical invocation. If CI ever standardizes on strict-only, the pin silently weakens. Suggested hardening: have this one test clear `LANGGRAPH_STRICT_MSGPACK` via `monkeypatch.delenv(..., raising=False)`, or document that the suite must also run in the default environment. Optional; today the suite passes in both modes and I ran both.

### Responses to Prior Points
**B1: resolved.** Verified against the actual tree, not just the diff. `tests/test_checkpointing.py` lines 48–54 serialize an `UnregisteredState` through the default `review_checkpoint_serializer()` and assert the restored value is not an instance — exactly the pin requested. I confirmed the test passes as written (4/4 in `test_checkpointing.py` in the default environment; 69/69 full suite under `LANGGRAPH_STRICT_MSGPACK=true`), and confirmed it would fail under both plausible regressions by constructing the regressed serializers directly: `JsonPlusSerializer()` and `JsonPlusSerializer(allowed_msgpack_modules=True)` both reconstruct the unregistered type (`isinstance: True`), which flips the test's assertion. The boundary is now pinned, with the environment caveat recorded as S3.

**Re R1:** Confirmed and sound. `review_checkpoint_types` (checkpointing.py lines 48–53) is the single canonical source, and the drift guard (`test_every_domain_checkpoint_type_is_registered`, test file lines 57–67) reflects over `agent_review.models` and `agent_review.lifecycle`, correctly filtering by `__module__` so re-exports don't leak in, and correctly excluding `InvalidTransition` (a `ValueError`, not a `BaseModel`/`StrEnum`). Two observations, neither requiring change: (a) the guard forced registration of the abstract base `ProtocolModel` (checkpointing.py line 44) — harmless, since it has no fields and never appears in checkpointed state; registering it is simpler than special-casing the base in the guard. (b) The guard's coverage is scoped to those two modules by construction; a future domain type defined in a *new* module would evade it. That is inherent to the design and acceptable — the module list is one line to extend.

**Re R2:** Accepted — the retention is now justified. My S2 argued the deduplication was redundant *for LangGraph's consumption*; with `review_checkpoint_types` promoted to a public utility whose tuple may be consumed by callers that do care about order and uniqueness (e.g. passing the type set to third-party checkpointer constructors, per Known Concern 2), deterministic deduplication is part of the function's contract rather than dead weight. S2 is closed with no change requested.

Verification performed on the actual tree: `src/agent_review/checkpointing.py` and `tests/test_checkpointing.py` match the rebuttal diff verbatim; `workflow.py` still consumes only `in_memory_review_checkpointer` (lines 10 and 97–98); `uvx ruff check src tests` passes; full suite 69 passed under `LANGGRAPH_STRICT_MSGPACK=true`; checkpointing tests 4 passed in the default environment.

**Verdict:** APPROVE

## Post-Approval Follow-Up
S3 was addressed with an environment-independent constructor-spy test that
asserts `review_checkpoint_serializer()` always passes the canonical explicit
allowlist to `JsonPlusSerializer`. This pins factory behavior even when
LangGraph strict mode would independently reject unregistered classes.

Ruff passes, strict mode passes all 70 tests, and the package builds
successfully.
