# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja 1383 — normalize Vikunja review log naming
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Use Vikunja's absolute numeric database ID—not the project-relative `#N`
identifier—as every audit-log filename prefix and Review Request task ID. Rename
all existing logs to `<database-id>-<task-title-slug>.md`, repair internal
references, document the rule in the skill, and enforce it with a repository
test.

### Relevant Code / Diff
Complete rename map:

```text
vikunja-2-typed-protocol-models.md
  → 1358-define-typed-review-protocol-models.md
vikunja-3-deterministic-lifecycle.md
  → 1359-implement-deterministic-review-lifecycle.md
vikunja-4-langgraph-workflow.md
  → 1360-wrap-lifecycle-in-a-langgraph-workflow.md
vikunja-5-simple-artifact-audit.md
  → 1361-add-simple-artifact-and-audit-logging.md
vikunja-6-model-adapters.md
  → 1362-add-worker-and-reviewer-model-adapters.md
vikunja-7-durable-persistence.md
  → 1363-add-durable-persistence-and-human-resume.md
vikunja-8-cli-service.md
  → 1364-expose-cli-and-thin-agent-skill.md
vikunja-9-clarify-protocol-semantics.md
  → 1369-clarify-blocking-protocol-semantics.md
vikunja-10-mcp-facade.md
  → 1370-add-mcp-facade-for-agent-review.md
vikunja-11-validated-state-replay.md
  → 1376-add-validated-review-state-replay.md
vikunja-12-checkpoint-factory.md
  → 1377-centralize-checkpoint-serializer-factory.md
vikunja-14-reviewer-config.md
  → 1379-add-reviewer-model-configuration-and-factory.md
vikunja-15-reviewer-integration.md
  → 1380-integrate-automatic-reviewer-generation.md
vikunja-16-global-config.md
  → 1381-move-reviewer-configuration-to-global-config-directory.md
vikunja-17-global-dotenv.md
  → 1382-load-reviewer-credentials-from-global-dotenv.md
```

Every log's task header was changed from `Vikunja #<relative>` to its absolute
database ID. The task-15 link was corrected to:

```text
agent_review/1379-add-reviewer-model-configuration-and-factory.md
```

Packaged and installed skill change:

```markdown
For Vikunja work, use the task's absolute numeric database ID as `task_id` and
the audit filename prefix (for example `1382-description.md`). Never use the
project-relative identifier such as `#17`.
```

Regression test:

```python
def test_vikunja_review_logs_use_absolute_database_ids() -> None:
    review_logs = tuple(REVIEW_LOG_ROOT.glob("*.md"))

    assert review_logs
    for review_log in review_logs:
        assert re.fullmatch(r"[0-9]+-[a-z0-9-]+\.md", review_log.name)
        task_line = review_log.read_text(encoding="utf-8").splitlines()[4]
        assert re.fullmatch(r"\*\*Task:\*\* Vikunja [0-9]+ — .+", task_line)
```

Repository-wide searches find no remaining `vikunja-<relative>-...` filenames
or `Vikunja #N` task headers.

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ env -u ANTHROPIC_API_KEY -u LLM_PROVIDER_KEY \
    LANGGRAPH_STRICT_MSGPACK=true uv run pytest -q
........................................................................ [ 46%]
........................................................................ [ 93%]
..........                                                               [100%]
154 passed in 1.03s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### Known Concerns
None.

### Specific Questions for Reviewer
1. Do all filenames use the correct absolute Vikunja database ID?
2. Are internal references and task headers consistent with the renamed files?
3. Does the regression test prevent returning to relative project identifiers?

## Review Response — Round 1
**Position:** AGREE

### Blocking Concerns
None.

Verification performed before this verdict:

1. Filenames (Q1): I listed project 190 ("Agent Review Application Assessment") via the read-only Vikunja API. Every claimed database ID is exact, including the non-linear jumps: #2→1358 … #8→1364, #9→1369, #10→1370, #11→1376, #12→1377, #14→1379, #15→1380, #16→1381, #17→1382, and 1383 for this task. The gap at #13 (id 1378, "Configure reviewer LLM provider and model") correctly has no log. All slugs match the actual task titles.
2. Headers and references (Q2): all 16 logs carry `**Task:** Vikunja <database-id> — <title>` on line 5. A repo-wide search finds no remaining `vikunja-<relative>-` filenames or `Vikunja #N` headers outside the intentional rename map inside the 1383 log itself. The corrected cross-log link in `1380-integrate-automatic-reviewer-generation.md` line 177 resolves to the existing `1379-add-reviewer-model-configuration-and-factory.md`. Git recorded the 13 previously tracked logs as renames (history preserved); `1381`/`1382` appear as new untracked files only because their `vikunja-16`/`vikunja-17` predecessors were never committed — no content lost.
3. Skill copies: the repo copy `skill/agent-review/SKILL.md` and the installed copy `~/.agents/skills/agent-review/SKILL.md` are byte-identical, both containing the new absolute-ID rule; the bundled protocol references are also identical.
4. Tests: `tests/test_skill.py` matches the diff under review; all 3 tests pass (`3 passed`, run with cache writes disabled).

### Non-Blocking Suggestions
S1. Regarding Question 3: the regression test only partially prevents a return to relative identifiers. It blocks the two historical failure modes (`vikunja-<n>-…` filenames and `Vikunja #N` headers), but a bare relative numeral would pass both regexes — `17-global-dotenv.md` matches `[0-9]+-[a-z0-9-]+\.md` and `**Task:** Vikunja 17 — …` matches the header pattern. A full offline distinction is impossible, but for this project a cheap tightening (e.g., require at least 4 digits, `[0-9]{4,}`) would catch every plausible relative ID (#1–#18) while all real database IDs are ≥1358.

S2. `review_log.read_text(...).splitlines()[4]` hard-codes the task header at line 5. It holds for all 16 current logs, but any future log with an extra leading line fails with an unrelated-looking assertion (or an `IndexError` on a short file). Searching all lines for the `**Task:**` prefix and validating that line would make failures self-explanatory.

S3. The test asserts every file in `agent_review/` is Vikunja-style, which implicitly forbids non-Vikunja logs (e.g., the protocol's own `DAI-42-…` example) in this repository. That is presumably intended for this repo, but worth being aware of if the workspace ever hosts reviews from another tracker.

**Verdict:** APPROVE

## Post-Approval Follow-Up
S1 and S2 were adopted: filename and task-header IDs now require at least four
digits, and the test discovers exactly one `**Task:**` line instead of assuming
line 5. S3 is intentional for this Vikunja-managed repository.

Final verification: Ruff passes, all 154 tests pass under strict checkpoint
mode, the package builds, and packaged/installed skill copies remain identical.
