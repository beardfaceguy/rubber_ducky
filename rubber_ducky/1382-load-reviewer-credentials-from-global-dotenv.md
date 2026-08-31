# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja 1382 — load generic reviewer credential from global dotenv
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Load a provider-neutral `LLM_PROVIDER_KEY` from one global `.env` beside global
`config.json`. Process environment values override file values; project dotenv
files are never searched. Parse without mutating `os.environ`, reject
key-without-value entries, and require no group/other permissions on POSIX.
Provider definitions default to the generic key name, including extensions.

### Relevant Code / Diff
Generic credential default:

```python
DEFAULT_PROVIDER_KEY_ENV = "LLM_PROVIDER_KEY"


@dataclass(frozen=True)
class ProviderDefinition:
    builder: ProviderBuilder
    requires_credential: bool = True
    default_api_key_env: str | None = DEFAULT_PROVIDER_KEY_ENV


_BUILTIN_PROVIDERS = {
    "openai": ProviderDefinition(_langchain_provider),
    "anthropic": ProviderDefinition(_langchain_provider),
}
```

Complete dotenv loader:

```python
def reviewer_environment(
    *,
    config_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
    home_directory: Path | None = None,
) -> dict[str, str]:
    """Merge global dotenv values with the process environment."""

    process_environment = dict(os.environ if environment is None else environment)
    resolved_config = config_path or reviewer_config_path(
        environment=process_environment,
        home_directory=home_directory,
    )
    dotenv_path = resolved_config.parent / ".env"
    file_environment: dict[str, str] = {}
    if dotenv_path.exists():
        if os.name == "posix":
            permissions = stat.S_IMODE(dotenv_path.stat().st_mode)
            if permissions & 0o077:
                raise ReviewerConfigurationError(
                    f"reviewer dotenv {dotenv_path} has insecure permissions "
                    f"{permissions:o}; require 600"
                )
        parsed = dotenv_values(dotenv_path)
        if any(value is None for value in parsed.values()):
            raise ReviewerConfigurationError(
                f"reviewer dotenv {dotenv_path} contains a key without a value"
            )
        file_environment = {
            key: value for key, value in parsed.items() if value is not None
        }
    return {**file_environment, **process_environment}
```

Factory/config integration:

```diff
 from dotenv import dotenv_values
@@
 class ReviewerModelFactory:
     def create(...):
-        env = os.environ if environment is None else environment
+        env = reviewer_environment() if environment is None else dict(environment)
@@
 def load_reviewer_config(...):
-    env = os.environ if environment is None else environment
+    env = reviewer_environment(
+        config_path=config_path,
+        environment=environment,
+        home_directory=home_directory,
+    )
```

Repository safety/examples:

```diff
--- a/.gitignore
+++ b/.gitignore
 .venv/
+.env

--- /dev/null
+++ b/examples/.env.example
+LLM_PROVIDER_KEY=replace-with-your-key

--- a/examples/config.json
+++ b/examples/config.json
-    "api_key_env": "ANTHROPIC_API_KEY",
+    "api_key_env": "LLM_PROVIDER_KEY",
```

README and packaged/installed skill state:

```text
- Global .env is the sibling of global config.json.
- Absolute XDG_CONFIG_HOME wins; otherwise ~/.config/agent_review/.env.
- The generic credential name is LLM_PROVIDER_KEY for every provider.
- Process environment overrides .env.
- Project .env files are ignored.
- Credential values never enter config, persistence, or audit metadata.
```

Runtime installation performed without printing the credential:

```text
~/.config/agent_review/config.json: api_key_env = LLM_PROVIDER_KEY
~/.config/agent_review/.env: contains LLM_PROVIDER_KEY
both files mode 600
```

Safe validation removed both `ANTHROPIC_API_KEY` and `LLM_PROVIDER_KEY` from the
process environment. The application loaded the generic key from global dotenv
and constructed `ChatAnthropic` for provider `anthropic`, model
`claude-fable-5`.

Tests verify:

- Global dotenv loading and process override.
- Generic `LLM_PROVIDER_KEY` defaults for OpenAI and Anthropic.
- Explicit custom credential env-name overrides still work.
- Local providers can opt out of credentials.
- POSIX group/other permissions are rejected.
- Global config/docs/skill use only the generic default.

Dependency:

```diff
+"python-dotenv>=1.2.3"
```

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ env -u ANTHROPIC_API_KEY -u LLM_PROVIDER_KEY \
    LANGGRAPH_STRICT_MSGPACK=true uv run pytest -q
........................................................................ [ 48%]
........................................................................ [ 96%]
......                                                                   [100%]
150 passed in 0.96s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### Known Concerns
1. The installed key must still be rotated because part of its previous value
   appeared in earlier validation output.
2. Environment interpolation supported by python-dotenv is enabled; values are
   not logged or persisted.
3. POSIX permission checks follow symlinks and validate the target mode.

### Specific Questions for Reviewer
1. Is credential lookup provider-neutral throughout runtime and documentation?
2. Can global dotenv values leak into configuration serialization, persistence,
   logs, or structured outputs?
3. Does runtime lookup avoid all per-project dotenv discovery?

## Review Response — Round 1
**Position:** PARTIAL

### Blocking Concerns

B1. **All dotenv values — not just credentials — flow into provider/model selection and config-path resolution, silently defeating the documented same-source invariant.** In `load_reviewer_config` (`src/agent_review/reviewer_config.py` lines 277–299), `env` is the merged mapping from `reviewer_environment`, and it is then used for (a) the `AGENT_REVIEW_REVIEWER_PROVIDER`/`AGENT_REVIEW_REVIEWER_MODEL` precedence tier, (b) `AGENT_REVIEW_REVIEWER_API_KEY_ENV`, and (c) `reviewer_config_path(environment=env, ...)` at line 296. Two concrete consequences:
   - A user can set `AGENT_REVIEW_REVIEWER_PROVIDER` in the real environment and `AGENT_REVIEW_REVIEWER_MODEL` in the global `.env` (or vice versa). The merged mapping presents both as a single "environment" source, so the "provider and model must come together from one precedence source" check at lines 291–294 passes on a genuinely mixed pairing — exactly the cross-source mixing the README, SKILL.md, and this task's own scope statement forbid. The task states the `.env` exists to hold the credential; nothing filters non-credential selection variables out of the merge.
   - If the global `.env` contains `XDG_CONFIG_HOME`, resolution becomes self-referential: `reviewer_environment` resolves the dotenv location from the *process* environment (line 212), but line 296 re-resolves the config path from the *merged* environment, so `config.json` can be read from a different directory than the `.env` that was just loaded beside it.

   Why it matters: this silently reintroduces a forbidden misconfiguration class and makes the loader's behavior depend on non-credential keys in a file documented as credential storage. The fix is small — restrict the merged file values to credential lookup (e.g., consult `file_environment` only in `ReviewerModelFactory.create` / for the resolved `api_key_env`), or strip `AGENT_REVIEW_REVIEWER_PROVIDER`, `AGENT_REVIEW_REVIEWER_MODEL`, and `XDG_CONFIG_HOME` from `file_environment` before merging, with a test pinning whichever rule you choose.

### Non-Blocking Suggestions

S1. `dotenv_values` is called with default `interpolate=True`, and python-dotenv's interpolation falls back to the real `os.environ` even when a caller supplies an explicit `environment` mapping. This is a determinism leak for the injected-environment test seam and a mild surprise surface (`${VAR}` expansion in a credentials file). You flagged this yourself (Known Concern 2); passing `interpolate=False` would remove the behavior at no cost unless you specifically want expansion — if you keep it, document it where the `.env` format is described.

S2. When an explicit `config_path` is passed (the test seam), `reviewer_environment` loads the sibling `.env` of *that* path. CLI and MCP never pass `config_path` (verified in `cli.py` lines 115–120 and `mcp_server.py`), so the "project `.env` files are ignored" guarantee holds at runtime, but the claim is only structurally true. A test asserting that a `.env` in the current working directory is never read would pin the guarantee against regression (e.g., someone later adding `find_dotenv`).

S3. `dotenv_path.stat()` and `dotenv_values` can raise raw `OSError` (e.g., unreadable file, or the file vanishing between `exists()` and `stat()`), escaping the `ReviewerConfigurationError` taxonomy the rest of the module maintains. Wrapping these would keep CLI exit-code mapping consistent. Related nit: the error message says "require 600" but the check actually accepts any mode with no group/other bits (e.g., `400`, `700`) — the check matches the task spec; the message is slightly narrower than the behavior.

### Verified

Everything else checks out against the actual code:

- `reviewer_environment` matches the submitted code verbatim; it never mutates `os.environ`, uses only the explicit global path (no `find_dotenv`/`load_dotenv` anywhere in `src/`), rejects group/other permission bits on POSIX, and rejects key-without-value entries. This answers Question 3 affirmatively for runtime (with the S2 caveat on the explicit `config_path` seam).
- Question 1 (provider neutrality): yes. `DEFAULT_PROVIDER_KEY_ENV = "LLM_PROVIDER_KEY"` is the default on `ProviderDefinition`, inherited by both built-ins and extensions; `examples/config.json`, `examples/.env.example`, README, packaged SKILL.md, and the installed skill all use only the generic name. The installed skill is byte-identical to the packaged one (verified by diff). The global `~/.config/agent_review/config.json` sets `api_key_env = LLM_PROVIDER_KEY` (I checked that key name only), and both global files are mode `600`, user-owned.
- Question 2 (leakage): I found no path for dotenv values into serialization, persistence, or audit. `ReviewerModelConfig` rejects secret-shaped option keys, `audit_metadata()` emits only provider and model, the credential reaches only the builder call, error messages include the dotenv path and mode but never values, and `test_reviewer_config_never_contains_environment_secret` pins the serialization guarantee. The one indirect exception is B1: `.env`-sourced provider/model values would land in audit metadata — not a credential leak, but part of why B1 should be closed.
- Dependency: `pyproject.toml` requires `python-dotenv>=1.2.3` and `uv.lock` resolves 1.2.3 with hashes. CLI and MCP both dropped the workspace argument to `load_reviewer_config` and pass no `environment`, so the global dotenv is exercised on the real runtime path.
- Tests cover dotenv load with process override, permission rejection, generic default for both built-in providers (parametrized), custom `api_key_env` override, credential-free local provider, and whitespace-credential rejection. As a read-only reviewer I did not re-run the suite; your transcript shows 150 passing with both credential variables unset, which is consistent with the code I inspected.
- Known Concern 1 (rotating the previously exposed key) is operational and outside this diff; I agree it must happen and note it is not resolvable by code review.
- Known Concern 3: correct — `Path.stat()` follows symlinks, so the check validates the target's mode; a symlinked `.env` with a `600` target passes, which is reasonable.

**Verdict:** REVISE

## Rebuttal — Round 1
**Position:** AGREE

### Responses to Blocking Concerns
Re B1: ACCEPT — provider/model/config-path selection now uses only the real
process environment. The global dotenv merge is used solely by
`ReviewerModelFactory.create` to resolve the already-selected credential
variable name.

### Revised Code / Diff
```diff
--- a/src/agent_review/reviewer_config.py
+++ b/src/agent_review/reviewer_config.py
@@
-        parsed = dotenv_values(dotenv_path)
+        try:
+            parsed = dotenv_values(dotenv_path, interpolate=False)
+        except OSError as error:
+            raise ReviewerConfigurationError(
+                f"cannot read reviewer dotenv {dotenv_path}: {error}"
+            ) from error
@@
 def load_reviewer_config(...):
-    env = reviewer_environment(
-        config_path=config_path,
-        environment=environment,
-        home_directory=home_directory,
-    )
+    env = os.environ if environment is None else environment
```

The permission check and dotenv parse are in the same `try`, and the error now
requires no group/other permission bits rather than claiming only mode 600 is
accepted.

Regression tests:

```python
def test_dotenv_cannot_supply_provider_or_model_selection(...):
    # Global config selects openai/global-model.
    # Sibling dotenv tries anthropic/dotenv-model.
    config = load_reviewer_config(config_path=config_path, environment={})
    assert config.provider == "openai"
    assert config.model == "global-model"


def test_dotenv_interpolation_is_disabled(...):
    dotenv_path.write_text("LLM_PROVIDER_KEY=${SOURCE_KEY}\n")
    environment = reviewer_environment(
        config_path=config_path,
        environment={"SOURCE_KEY": "must-not-expand"},
    )
    assert environment["LLM_PROVIDER_KEY"] == "${SOURCE_KEY}"


def test_project_dotenv_is_never_discovered(...):
    monkeypatch.chdir(project_with_dotenv)
    environment = reviewer_environment(
        config_path=global_config,
        environment={},
    )
    assert "PROJECT_SECRET" not in environment
```

README and both skill copies now explicitly say the global dotenv is credential
input only; provider/model/XDG values there are ignored for selection, project
dotenv files are ignored, and interpolation is disabled.

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ env -u ANTHROPIC_API_KEY -u LLM_PROVIDER_KEY \
    LANGGRAPH_STRICT_MSGPACK=true uv run pytest -q
........................................................................ [ 47%]
........................................................................ [ 94%]
.........                                                                [100%]
153 passed in 0.91s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### New Points
R1. Adopted S1 by disabling dotenv interpolation.

R2. Adopted S2 with a current-working-directory project dotenv regression test.

R3. Adopted S3 by normalizing stat/parse `OSError` to
`ReviewerConfigurationError` and correcting the permissions wording.

**Requesting:** RE-REVIEW

## Review Response — Round 2
**Position:** AGREE

### Blocking Concerns
None.

### Non-Blocking Suggestions

S4. The runtime installation claimed in the Round-1 request no longer matches the machine state: `~/.config/agent_review/` now contains only `config.json` (mode `600`, `api_key_env = LLM_PROVIDER_KEY` — I checked that key name only) and a new `config.example.json` (mode `664`); the `.env` that Round 1 reported as installed with mode `600` is absent. This is presumably the key-rotation from Known Concern 1 in progress, and it is outside the reviewed diff, so it does not block. But until a rotated key is reinstalled in `~/.config/agent_review/.env` (mode `600`) or exported in the process environment, a real `agent-review review` invocation will fail with "reviewer credential environment variable 'LLM_PROVIDER_KEY' is not set". Confirm the rotation completes and the file is restored before relying on the runtime path.

### Responses to Prior Points

Re B1: **resolved.** Verified against `src/agent_review/reviewer_config.py` as it exists now. `load_reviewer_config` (line 283) uses `os.environ` or the caller-supplied mapping only — the dotenv merge is gone from selection entirely. Consequently: (a) the `AGENT_REVIEW_REVIEWER_PROVIDER`/`AGENT_REVIEW_REVIEWER_MODEL` pair (lines 285–288) and `AGENT_REVIEW_REVIEWER_API_KEY_ENV` (line 314) can no longer be sourced or half-sourced from the global `.env`, so the same-source check at lines 306–309 operates on genuinely single-source pairs; (b) `reviewer_config_path(environment=env, ...)` at line 298 sees only the process environment, eliminating the self-referential `XDG_CONFIG_HOME` path. The merged mapping now reaches exactly one consumer: `ReviewerModelFactory.create` (line 167), which consults it solely via `env.get(api_key_env)` for the already-selected credential name. `test_dotenv_cannot_supply_provider_or_model_selection` pins the rule with a dotenv that attempts to override both provider and model and loses to the global config. CLI (`cli.py` lines 115–125) and MCP (`mcp_server.py` lines 58–61) still pass no `environment`, so the dotenv-backed credential lookup remains exercised on the real runtime path.

Re R1: confirmed. `dotenv_values(dotenv_path, interpolate=False)` at line 228; `test_dotenv_interpolation_is_disabled` asserts `${SOURCE_KEY}` survives literally; README ("variable interpolation is disabled") and both SKILL.md copies ("variable interpolation is off", byte-identical packaged vs. installed, and now pinned by `test_skill.py` line 19) document it.

Re R2: confirmed. `test_project_dotenv_is_never_discovered` chdirs into a project containing a `.env` with `PROJECT_SECRET` and asserts it never enters the merged environment. Combined with the continued absence of `find_dotenv`/`load_dotenv` anywhere in `src/`, this pins the guarantee I asked for in S2.

Re R3: confirmed. The permission check and parse share one `try` (lines 219–232); `OSError` from `stat()` or the parse is re-raised as `ReviewerConfigurationError` with the path but no values, keeping the CLI exit-code taxonomy intact. (`ReviewerConfigurationError` is a `ValueError`, so the permission rejection raised inside the same `try` correctly escapes the `except OSError` handler.) The message now reads "require no group/other permissions (for example 600)", matching the actual check.

S1–S3 are all adopted and closed. Verified state otherwise unchanged from Round 1: no leakage path for dotenv values into serialization, persistence, or audit metadata; `pyproject.toml` pins `python-dotenv>=1.2.3` with `uv.lock` resolving it; `.gitignore` covers `.env`; `examples/.env.example` contains only the placeholder line. As a read-only reviewer I did not run the suite; your transcript's 153 passing tests are consistent with the three new tests I inspected.

**Verdict:** APPROVE

## Post-Approval Follow-Up
S4 was checked directly after review: `~/.config/agent_review/.env` exists and
is mode `600`, alongside mode-`600` `config.json`. The reviewer observation was
caused by hidden-file visibility during inspection; no reinstall was required.

Final verification: Ruff passes, all 153 tests pass with both provider key
variables removed from the process environment, the package builds, and the
generic dotenv successfully constructs the configured Anthropic model.
