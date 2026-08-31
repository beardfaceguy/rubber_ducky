# Agent Review Log
**Protocol:** review-protocol.md v1.3

## Review Request — Round 1
**Task:** Vikunja 1379 — add reviewer model configuration and factory
**Protocol:** review-protocol.md v1.3 — respond using the Review Response format.

### Proposed Solution
Add an immutable, secret-safe reviewer configuration with explicit →
environment → workspace-file precedence. No model has a default. A trusted
provider registry supplies OpenAI and Anthropic builders and accepts explicit
local extensions. Credentials are read only at construction time from a named
environment variable. Audit metadata contains only provider and model.

### Relevant Code / Diff
Complete new `src/agent_review/reviewer_config.py`:

```python
"""Validated reviewer model settings and provider-neutral factory."""

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from langchain.chat_models import init_chat_model
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
)

from agent_review.adapters import StructuredOutputModel

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

_SECRET_OPTION_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "auth_token",
    "token",
    "secret",
    "password",
    "credential",
    "credentials",
}
_SECRET_OPTION_SUFFIXES = ("_api_key", "_access_token", "_auth_token", "_password")


def _is_secret_option_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in _SECRET_OPTION_KEYS or normalized.endswith(
        _SECRET_OPTION_SUFFIXES
    )


def _contains_secret_key(value: JsonValue) -> bool:
    if isinstance(value, dict):
        return any(
            _is_secret_option_key(key) or _contains_secret_key(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


class ReviewerModelConfig(BaseModel):
    """Serializable reviewer selection without credential values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: NonEmptyText
    model: NonEmptyText
    api_key_env: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")] | None = None
    options: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.lower()

    @field_validator("options")
    @classmethod
    def reject_persisted_secrets(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if _contains_secret_key(value):
            raise ValueError("reviewer options cannot contain secret values")
        return value

    def audit_metadata(self) -> dict[str, str]:
        """Return reproducibility metadata without credential references."""

        return {
            "provider": self.provider,
            "model": self.model,
        }


class ReviewerConfigurationError(ValueError):
    """Raised when reviewer model configuration cannot be used."""


class UnsupportedReviewerProvider(ReviewerConfigurationError):
    """Raised when no trusted builder is registered for a provider."""


ProviderBuilder = Callable[[ReviewerModelConfig, str | None], StructuredOutputModel]


@dataclass(frozen=True)
class ProviderDefinition:
    """Trusted provider constructor and credential requirements."""

    builder: ProviderBuilder
    requires_credential: bool = True
    default_api_key_env: str | None = None


def _langchain_provider(
    config: ReviewerModelConfig,
    credential: str | None,
) -> StructuredOutputModel:
    options = dict(config.options)
    if credential is not None:
        options["api_key"] = credential
    return init_chat_model(
        model=config.model,
        model_provider=config.provider,
        **options,
    )


_BUILTIN_PROVIDERS = {
    "openai": ProviderDefinition(
        _langchain_provider,
        default_api_key_env="OPENAI_API_KEY",
    ),
    "anthropic": ProviderDefinition(
        _langchain_provider,
        default_api_key_env="ANTHROPIC_API_KEY",
    ),
}


class ReviewerModelFactory:
    """Construct reviewer models from an explicit trusted provider registry."""

    def __init__(
        self,
        additional_providers: Mapping[str, ProviderDefinition] | None = None,
    ) -> None:
        self._providers = dict(_BUILTIN_PROVIDERS)
        for name, definition in (additional_providers or {}).items():
            normalized_name = name.strip().lower()
            if not normalized_name:
                raise ValueError("provider extension name cannot be empty")
            if normalized_name in self._providers:
                raise ValueError(f"provider {normalized_name!r} is already registered")
            self._providers[normalized_name] = definition

    def create(
        self,
        config: ReviewerModelConfig,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> StructuredOutputModel:
        env = os.environ if environment is None else environment
        definition = self._providers.get(config.provider)
        if definition is None:
            supported = ", ".join(sorted(self._providers))
            raise UnsupportedReviewerProvider(
                f"unsupported reviewer provider {config.provider!r}; "
                f"supported providers: {supported}"
            )
        api_key_env = config.api_key_env or definition.default_api_key_env
        credential = env.get(api_key_env) if api_key_env is not None else None
        if definition.requires_credential and credential is None:
            raise ReviewerConfigurationError(
                f"reviewer credential environment variable {api_key_env!r} is not set"
            )
        return definition.builder(config, credential)


def load_reviewer_config(
    workspace_root: Path,
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key_env: str | None = None,
    options: Mapping[str, Any] | None = None,
    environment: Mapping[str, str] | None = None,
) -> ReviewerModelConfig:
    """Load explicit, environment, then workspace reviewer settings."""

    env = os.environ if environment is None else environment
    config_path = workspace_root / "agent_review" / "config.json"
    file_values: dict[str, Any] = {}
    if config_path.exists():
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("config.json must contain an object")
        reviewer = payload.get("reviewer", {})
        if not isinstance(reviewer, dict):
            raise TypeError("config.json reviewer must be an object")
        unknown_fields = set(reviewer) - {
            "provider",
            "model",
            "api_key_env",
            "options",
        }
        if unknown_fields:
            raise ValueError(f"unknown reviewer settings: {sorted(unknown_fields)}")
        file_values = reviewer

    resolved_provider = (
        provider
        or env.get("AGENT_REVIEW_REVIEWER_PROVIDER")
        or file_values.get("provider")
    )
    resolved_model = (
        model or env.get("AGENT_REVIEW_REVIEWER_MODEL") or file_values.get("model")
    )
    if not resolved_provider or not resolved_model:
        raise ValueError("reviewer provider and model must be configured")
    normalized_provider = str(resolved_provider).lower()
    provider_definition = _BUILTIN_PROVIDERS.get(normalized_provider)
    resolved_api_key_env = (
        api_key_env
        or env.get("AGENT_REVIEW_REVIEWER_API_KEY_ENV")
        or file_values.get("api_key_env")
        or (
            provider_definition.default_api_key_env
            if provider_definition is not None
            else None
        )
    )
    file_options = file_values.get("options", {})
    if not isinstance(file_options, dict):
        raise TypeError("config.json reviewer.options must be an object")
    merged_options = {**file_options, **(options or {})}
    return ReviewerModelConfig(
        provider=normalized_provider,
        model=str(resolved_model),
        api_key_env=(
            str(resolved_api_key_env) if resolved_api_key_env is not None else None
        ),
        options=merged_options,
    )
```

Complete tests are in `tests/test_reviewer_config.py` and cover file/env/explicit
precedence, unknown fields, nested secret-key rejection, normal token-count
options, secret-free serialization/audit metadata, network-free builder
selection, unsupported providers, explicit local extension, and OpenAI and
Anthropic credential requirements.

Dependency changes:

```toml
dependencies = [
    "langchain>=1.3.16",
]

[project.optional-dependencies]
openai = ["langchain-openai>=1.6.0"]
anthropic = ["langchain-anthropic>=1.6.1"]
```

Verification:

```text
$ uvx ruff check src tests
All checks passed!

$ LANGGRAPH_STRICT_MSGPACK=true uv run pytest -q
........................................................................ [ 54%]
............................................................             [100%]
132 passed in 0.87s

$ uv build
Successfully built dist/agent_review-0.1.0.tar.gz
Successfully built dist/agent_review-0.1.0-py3-none-any.whl
```

### Known Concerns
1. Provider packages are optional extras; selecting a built-in without its
   integration installed produces LangChain's import error.
2. Provider-specific options are validated as JSON and secret-key-free but are
   otherwise passed through to the integration.
3. Configuration is not yet invoked by service/CLI/MCP; that is task #15.

### Specific Questions for Reviewer
1. Is precedence deterministic and free of hidden model defaults?
2. Are credential values excluded from persistent and audit-safe configuration?
3. Does the provider registry allow controlled extension without permitting
   unsupported provider strings to reach LangChain?

## Review Response — Round 1
**Position:** AGREE

Verification notes: `src/agent_review/reviewer_config.py` and `tests/test_reviewer_config.py` on disk match the request exactly. Answering the specific questions: (1) precedence is deterministic per field — explicit → `AGENT_REVIEW_REVIEWER_*` env → `agent_review/config.json` — and there is no default model anywhere; absence raises. (2) Credential values never enter `ReviewerModelConfig`: only the env-var *name* is stored, the options validator rejects secret-shaped keys recursively (including in file-sourced options, since they pass through the same validator), `audit_metadata()` exposes only provider and model, and the test asserting the secret is absent from `model_dump_json()` confirms it. (3) The registry gates dispatch before LangChain is touched — unsupported providers raise `UnsupportedReviewerProvider` with the supported list, extensions are explicit constructor arguments, and builtin-name collisions are rejected; extension defaults for `api_key_env` are correctly applied at `create()` time even though `load_reviewer_config` only knows builtins.

### Blocking Concerns
None.

### Non-Blocking Suggestions
S1. Error-type consistency: `load_reviewer_config` raises bare `ValueError`/`TypeError` (unknown fields, missing provider/model, malformed file), while the factory raises `ReviewerConfigurationError`. Since `ReviewerConfigurationError` subclasses `ValueError`, a task-#15 caller catching `ReviewerConfigurationError` will miss all load-time failures. Consider raising `ReviewerConfigurationError` from the load path too.

S2. Per-field precedence can silently mix sources: env `AGENT_REVIEW_REVIEWER_PROVIDER=anthropic` combined with a workspace file that only pins an OpenAI model yields provider=anthropic, model=file-model. Deterministic, but a mismatched pair is easy to produce. Consider resolving provider+model as a unit (first source that supplies both wins) or documenting the per-field behavior explicitly.

S3. The secret-key denylist misses common credential key shapes: `authorization`, `bearer_token`, `session_token` (the suffix list has `_api_key`/`_auth_token` but not `_token` or `_secret`). A denylist can never be complete, but broadening the suffixes to `_token` and `_secret` and adding `authorization` closes the most likely gaps cheaply.

S4. `definition.requires_credential and credential is None` treats an empty-string env var as a present credential and forwards `api_key=""` to the integration, producing a downstream auth error instead of the clear configuration error. Treating empty/whitespace as missing would surface the misconfiguration at construction time.

**Verdict:** APPROVE

## Post-Approval Follow-Up
All suggestions were adopted:

- S1: every workspace-load failure is normalized to
  `ReviewerConfigurationError`.
- S2: provider and model are resolved as an inseparable pair from one source.
- S3: common authorization, token, and secret key shapes are rejected without
  blocking normal options such as `max_tokens`.
- S4: empty and whitespace credentials are treated as missing.

Ruff passes and all 14 configuration tests pass.
