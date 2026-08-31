# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja 1380 — integrate automatic reviewer generation
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Generate reviewer responses through the approved `ReviewerModelFactory` and
`ReviewerAdapter`, then submit through the existing journal → audit → graph
service path. Persist only provider/model metadata, audit it under escaped
`reviewer.*` attributes, and make generated-event retries skip repeated model
calls. Add identical CLI and MCP configuration overrides plus workspace/env
fallback.

### Relevant Code / Diff
New service method and metadata-aware submit path:

```python
def submit(
    self,
    thread_id: str,
    event_id: str,
    event: ReviewEvent,
    *,
    metadata: Mapping[str, str] | None = None,
) -> ReviewState:
    result = self.store.append_event_once(
        thread_id,
        event_id,
        event,
        metadata=metadata,
    )
    history = self.store.load_history(thread_id)
    audit = self._open_audit(thread_id, history)
    if result.appended or not self.store.is_event_audited(thread_id, event_id):
        try:
            audit.append(
                event,
                event_id=event_id,
                audit_metadata={
                    f"reviewer.{key}": value
                    for key, value in (metadata or {}).items()
                },
            )
        except ArtifactConflict as error:
            raise PersistenceConflict(str(error)) from error
        self.store.mark_event_audited(thread_id, event_id)
    self._reconcile_graph(thread_id, history, result.state)
    return result.state


def generate_review(
    self,
    thread_id: str,
    event_id: str,
    config: ReviewerModelConfig,
    *,
    factory: ReviewerModelFactory | None = None,
    environment: Mapping[str, str] | None = None,
) -> ReviewState:
    """Generate, validate, persist, and audit one reviewer response."""

    metadata = config.audit_metadata()
    history = self.store.load_history(thread_id)
    for stored in history.events:
        if stored.event_id == event_id:
            if stored.metadata != metadata:
                raise PersistenceConflict(
                    f"event ID {event_id!r} was reused with different "
                    "reviewer configuration"
                )
            return self.status(thread_id)

    state = self.status(thread_id)
    model = (factory or ReviewerModelFactory()).create(
        config,
        environment=environment,
    )
    response = ReviewerAdapter(model).review(state)
    return self.submit(
        thread_id,
        event_id,
        response,
        metadata=metadata,
    )
```

Persistence metadata changes:

```diff
 class StoredEvent:
     event_id: str
     event: ReviewEvent
+    metadata: dict[str, str]

 CREATE TABLE review_events (
     ...
+    metadata_json TEXT NOT NULL DEFAULT '{}'
 );

+Migration adds metadata_json to existing databases when absent.
+append_event/append_event_once accept metadata.
+Idempotency compares event type, payload, and canonical metadata JSON.
+Only provider/model keys with non-empty string values are accepted.
+load_history validates metadata shape before returning StoredEvent.
```

Audit metadata changes:

```diff
+AuditLog.append(..., audit_metadata: Mapping[str, str] | None)
+Metadata keys use a strict pattern.
+Values are HTML-escaped and double-hyphens are neutralized.
+Rendered event comments contain reviewer.provider and reviewer.model only.
```

CLI integration:

```python
review = commands.add_parser("review")
review.add_argument("thread_id")
review.add_argument("event_id")
review.add_argument("--provider")
review.add_argument("--model")
review.add_argument("--api-key-env")
review.add_argument("--option", action="append", default=[])

# In main:
config = load_reviewer_config(
    arguments.workspace.resolve(),
    provider=arguments.provider,
    model=arguments.model,
    api_key_env=arguments.api_key_env,
    options=_parse_options(arguments.option),
)
state = service.generate_review(
    arguments.thread_id,
    arguments.event_id,
    config,
)
```

MCP integration:

```python
@server.tool(
    name="agent_review_generate",
    description="Generate and apply a reviewer response with configured provider/model.",
    annotations=_IDEMPOTENT_WRITE,
    structured_output=True,
)
def generate_review_response(
    workspace: str,
    thread_id: str,
    event_id: str,
    provider: str | None = None,
    model: str | None = None,
    api_key_env: str | None = None,
    options: dict[str, JsonValue] | None = None,
) -> ReviewToolResult:
    return _execute(
        thread_id,
        lambda: _generate_review(
            workspace,
            thread_id,
            event_id,
            provider,
            model,
            api_key_env,
            options,
        ),
    )
```

Configuration uses the complete reviewed implementation recorded in
`agent_review/1379-add-reviewer-model-configuration-and-factory.md`, including
its post-approval
hardening. README and both skill copies document:

```text
explicit CLI/MCP provider+model
  → AGENT_REVIEW_REVIEWER_PROVIDER + AGENT_REVIEW_REVIEWER_MODEL
  → agent_review/config.json
```

Credentials remain environment-only. OpenAI and Anthropic integrations are
optional extras; no model name has a default.

Tests cover:

- Configured fake/offline reviewer generation without network access.
- Provider/model persistence and escaped audit metadata.
- Credential delivery to the builder without database/log persistence.
- Generated-event retry without a second model invocation.
- Event metadata validation and idempotency.
- CLI explicit provider/model/options.
- MCP `agent_review_generate` schema and overrides.
- Updated skill and README contracts.

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ LANGGRAPH_STRICT_MSGPACK=true uv run pytest -q
........................................................................ [ 50%]
.......................................................................  [100%]
143 passed in 0.85s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl

$ uv run agent-review-mcp </dev/null
exit 0
```

### Known Concerns
1. Real provider requests are not executed in tests; factories are monkeypatched
   or use an offline extension.
2. Model identifiers are provider-defined strings and cannot be verified
   without contacting the provider.
3. Markdown audit projection is still at-least-once; canonical event metadata
   in SQLite is exactly idempotent by event ID.

### Specific Questions for Reviewer
1. Can credentials enter persisted state, audit logs, or structured outputs?
2. Is provider/model resolution identical across CLI and MCP?
3. Does generated-response retry avoid repeated model cost and nondeterminism
   while preserving event idempotency?

## Review Response — Round 1
**Position:** PARTIAL

Verification notes: the working-tree code matches the Review Request excerpts exactly — `service.py` `submit`/`generate_review`, the persistence `metadata_json` column plus `PRAGMA table_info` migration and `_validate_metadata` (provider/model only, non-empty strings, canonical sorted JSON in the idempotency comparison), the audit `_metadata` attribute rendering (`fullmatch` key pattern, `html.escape(quote=True)` plus `--` neutralization), the CLI `review` subcommand, the MCP `agent_review_generate` tool, and the README/skill precedence text. The `reviewer_config.py` on disk includes all four task-14 post-approval hardenings (normalized `ReviewerConfigurationError`, provider+model pair resolution from a single source, broadened secret-key denylist, whitespace credentials treated as missing). I did not execute the test suite (read-only reviewer), but the reported count is corroborated: 141 collected test functions plus 2 parametrized expansions equals the reported 143.

Answering the specific questions:

1. **Credentials:** No leak path found. The credential value exists only between `ReviewerModelFactory.create` reading the named env var and the builder call; `ReviewerModelConfig` stores only the env-var *name*, persistence rejects any metadata key other than `provider`/`model`, the audit comment renders only those two escaped values, `ReviewToolResult` carries only thread/state/expected-event, and `test_generated_review_never_persists_runtime_credential` asserts the secret is absent from both the SQLite bytes and the Markdown log.
2. **CLI/MCP parity:** Yes. Both resolve the workspace path, then call the same `load_reviewer_config` with the same four explicit overrides against `os.environ` and the same `agent_review/config.json`; the only differences are option transport (`KEY=JSON` vs. dict) and that MCP requires an explicit workspace where the CLI defaults to cwd, consistent with every other command.
3. **Retry semantics:** The happy-path retry is verified (`model.calls == 1`, identical returned state, `PersistenceConflict` on metadata mismatch). However the retry path has a durability hole — see B1.

### Blocking Concerns
B1. `generate_review`'s duplicate-event early return (`src/agent_review/service.py` lines 155–162) bypasses the audit backfill that `submit` performs (lines 126–138), so a generated response can be permanently lost from the Markdown audit log. Concrete failure: first call journals the event via `append_event_once`, then `audit.append` fails (e.g. `ArtifactConflict`, transient `OSError`) or the process dies before `mark_event_audited` — the CLI/MCP caller sees an error and retries the same `agent-review review` / `agent_review_generate` call, which now finds the stored event, matches metadata, and returns success via `self.status(...)` without ever checking `is_event_audited` or re-appending the audit entry. Nothing else backfills it: `status` and `start` never audit protocol events, and the caller never has the response object to re-`submit`. This contradicts `submit`'s documented guarantee ("produce a visible duplicate entry on retry rather than lose evidence"), the protocol's requirement that the log record every message, and the request's own claim that only the *model call* is skipped on retry. Minimal fix: on the matched-event path, route through the idempotent submit with the stored event — `return self.submit(thread_id, event_id, stored.event, metadata=stored.metadata)` — which skips the model, dedupes the journal append, and backfills the audit marker; add a test that fails `audit.append` on first attempt and asserts the retry writes the log entry.

### Non-Blocking Suggestions
S1. `agent_review_generate` reuses the shared `_IDEMPOTENT_WRITE` annotation with `openWorldHint=False`, but unlike every other tool it contacts an external model provider over the network. Give it its own annotation with `openWorldHint=True` so MCP clients get an accurate safety signal.

S2. Two concurrent `generate_review` calls with the same event ID both pass the history scan, so both invoke the model (double cost) and, because model output is nondeterministic, the loser fails with `PersistenceConflict("reused with different data")` rather than deduplicating. Correctness is preserved; worth a docstring note or an advisory guard if concurrent workers are expected.

S3. The README/skill precedence text ("explicit → env → config.json") does not mention the task-14 hardening that provider and model must come *together* from one source. A user who sets only `AGENT_REVIEW_REVIEWER_MODEL` with the provider in `config.json` gets "environment must configure reviewer provider and model together", which the docs don't predict. One sentence in the README closes the gap.

**Verdict:** REVISE

## Rebuttal — Round 1
**Position:** AGREE

### Responses to Blocking Concerns
Re B1: ACCEPT — duplicate generated events now route through `submit` with the
stored event and metadata. This skips model invocation while restoring any
missing audit projection before graph reconciliation.

### Revised Code / Diff
```diff
--- a/src/agent_review/service.py
+++ b/src/agent_review/service.py
@@
         for stored in history.events:
             if stored.event_id == event_id:
                 if stored.metadata != metadata:
                     raise PersistenceConflict(
                         f"event ID {event_id!r} was reused with different "
                         "reviewer configuration"
                     )
-                return self.status(thread_id)
+                return self.submit(
+                    thread_id,
+                    event_id,
+                    stored.event,
+                    metadata=stored.metadata,
+                )
@@
-        """Generate, validate, persist, and audit one reviewer response."""
+        """Generate, validate, persist, and audit one reviewer response.
+
+        Retrying a persisted event skips model invocation. Concurrent first
+        attempts can both invoke the provider; event idempotency still permits
+        only one result to become canonical.
+        """

--- a/src/agent_review/mcp_server.py
+++ b/src/agent_review/mcp_server.py
@@
+_IDEMPOTENT_MODEL_WRITE = ToolAnnotations(
+    readOnlyHint=False,
+    destructiveHint=False,
+    idempotentHint=True,
+    openWorldHint=True,
+)
@@
-    annotations=_IDEMPOTENT_WRITE,
+    annotations=_IDEMPOTENT_MODEL_WRITE,

--- a/README.md
+++ b/README.md
@@
-then `agent_review/config.json`:
+then `agent_review/config.json`. Provider and model must be supplied together by
+the same precedence source; values are never mixed across sources.

--- a/skill/agent-review/SKILL.md
+++ b/skill/agent-review/SKILL.md
@@
 Credentials are read from the configured environment-variable name and must
-never be written to input JSON or audit logs.
+never be written to input JSON or audit logs. Provider and model must come
+together from one precedence source; do not mix values across sources.
```

Regression coverage:

```python
def test_generated_review_retry_backfills_failed_audit(...):
    # First call journals the generated response, then simulated audit failure.
    with pytest.raises(OSError):
        service.generate_review(...)

    # Retry uses the stored response, does not call the model again, and writes
    # the missing Markdown response.
    recovered = service.generate_review(...)
    assert recovered.status is ReviewStatus.APPROVED
    assert model.calls == 1
    assert "## Review Response — Round 1" in log
```

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ LANGGRAPH_STRICT_MSGPACK=true uv run pytest -q
........................................................................ [ 50%]
........................................................................ [100%]
144 passed in 1.00s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### New Points
R1. Adopted S1 with a dedicated open-world MCP annotation for model generation.

R2. Adopted S2 by documenting concurrent first-attempt behavior in the service
method; persistence correctness remains idempotent even if provider cost is
duplicated.

R3. Adopted S3 in both README and packaged/installed skill.

**Requesting:** RE-REVIEW

## Review Response — Round 2
**Position:** AGREE

Verification notes: I re-read the entire log, the task-14 log, and the actual working-tree files. The Rebuttal — Round 1 diff is applied verbatim: `generate_review`'s matched-event path now routes through `submit(thread_id, event_id, stored.event, metadata=stored.metadata)` (`src/agent_review/service.py` lines 160–172), the concurrency docstring is present (lines 151–156), `mcp_server.py` defines `_IDEMPOTENT_MODEL_WRITE` with `openWorldHint=True` (lines 88–93) and applies it only to `agent_review_generate` (line 148), and the pair-resolution sentence appears in `README.md` (lines 30–31) and the repository skill (lines 53–54). I diffed the repository skill directory against the installed copy at `~/.agents/skills/agent-review`: `SKILL.md` and `references/` are byte-identical. The new regression test `test_generated_review_retry_backfills_failed_audit` (`tests/test_service.py` lines 280–331) reproduces B1's exact failure — first attempt journals the event, then `AuditLog.append` raises `OSError` before `mark_event_audited`; the retry asserts `model.calls == 1` and that "## Review Response — Round 1" reaches the Markdown log — which is precisely the fix-plus-test I requested. The MCP test asserts `generate_tool.annotations.open_world_hint is True` while all tools keep idempotent/non-read-only hints (`tests/test_mcp_server.py` lines 23–28). Per the read-only reviewer constraint I did not execute the suite; the reported "144 passed" is corroborated by count: 142 collected test functions plus two 2-case parametrizations (`tests/test_reviewer_config.py` lines 210 and 237) yields 144.

### Blocking Concerns
None.

### Non-Blocking Suggestions
None new. S1, S2, and S3 are adopted and closed (see Re R1–R3 below).

### Responses to Prior Points
Re B1: **resolved.** The duplicate-event early return no longer bypasses audit backfill. The matched path re-enters `submit`, whose `result.appended or not is_event_audited(...)` guard re-appends the missing Markdown entry and sets the marker, while `append_event_once` dedupes the journal and the model is never re-invoked. The regression test fails the first audit append with a transient `OSError` and proves the retry backfills the log with `model.calls == 1` — exactly the durability scenario I described. The at-least-once audit guarantee documented in `submit`'s docstring now genuinely covers generated responses.

Re R1: Confirmed adopted. `agent_review_generate` is the only tool carrying `_IDEMPOTENT_MODEL_WRITE` with `openWorldHint=True`; the remaining five tools correctly keep the closed-world annotation, and the schema test locks this in. S1 closed.

Re R2: Confirmed adopted. The docstring accurately states that concurrent first attempts may both invoke the provider while event idempotency permits only one canonical result — which matches the actual behavior, since the loser's `append_event_once` will either dedupe (identical output) or raise `PersistenceConflict`. Documenting rather than locking is a reasonable scope call for single-worker usage. S2 closed.

Re R3: Confirmed adopted. The pair-resolution sentence is in the README, the repository skill, and — verified by byte-level diff — the installed skill copy, so a user setting only `AGENT_REVIEW_REVIEWER_MODEL` in the environment will now find the "together from one source" rule documented before hitting the error. S3 closed.

**Verdict:** APPROVE
