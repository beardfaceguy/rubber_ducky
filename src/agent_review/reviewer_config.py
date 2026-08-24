"""Validated reviewer model settings and provider-neutral factory."""

import json
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from dotenv import dotenv_values
from langchain.chat_models import init_chat_model
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    ValidationError,
    field_validator,
)

from agent_review.adapters import StructuredOutputModel

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
DEFAULT_PROVIDER_KEY_ENV = "LLM_PROVIDER_KEY"

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
    "authorization",
}
_SECRET_OPTION_SUFFIXES = (
    "_api_key",
    "_token",
    "_secret",
    "_password",
)


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
    default_api_key_env: str | None = DEFAULT_PROVIDER_KEY_ENV


def _langchain_provider(
    config: ReviewerModelConfig,
    credential: str | None,
) -> StructuredOutputModel:
    options = dict(config.options)
    if credential is not None:
        options["api_key"] = credential
    try:
        return init_chat_model(
            model=config.model,
            model_provider=config.provider,
            **options,
        )
    except ImportError as error:
        raise ReviewerConfigurationError(
            f"reviewer provider {config.provider!r} integration is not installed"
        ) from error


_BUILTIN_PROVIDERS = {
    "openai": ProviderDefinition(_langchain_provider),
    "anthropic": ProviderDefinition(_langchain_provider),
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
        env = reviewer_environment() if environment is None else dict(environment)
        definition = self._providers.get(config.provider)
        if definition is None:
            supported = ", ".join(sorted(self._providers))
            raise UnsupportedReviewerProvider(
                f"unsupported reviewer provider {config.provider!r}; "
                f"supported providers: {supported}"
            )
        api_key_env = config.api_key_env or definition.default_api_key_env
        credential = env.get(api_key_env) if api_key_env is not None else None
        if definition.requires_credential and (
            credential is None or not credential.strip()
        ):
            raise ReviewerConfigurationError(
                f"reviewer credential environment variable {api_key_env!r} is not set"
            )
        return definition.builder(config, credential)


def reviewer_config_path(
    *,
    environment: Mapping[str, str] | None = None,
    home_directory: Path | None = None,
) -> Path:
    """Return the global reviewer configuration path."""

    env = os.environ if environment is None else environment
    xdg_config_home = env.get("XDG_CONFIG_HOME")
    xdg_path = Path(xdg_config_home) if xdg_config_home else None
    if xdg_path is not None and xdg_path.is_absolute():
        config_home = xdg_path
    else:
        config_home = (home_directory or Path.home()) / ".config"
    return config_home / "agent_review" / "config.json"


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
        try:
            if os.name == "posix":
                permissions = stat.S_IMODE(dotenv_path.stat().st_mode)
                if permissions & 0o077:
                    raise ReviewerConfigurationError(
                        f"reviewer dotenv {dotenv_path} has insecure permissions "
                        f"{permissions:o}; require no group/other permissions "
                        "(for example 600)"
                    )
            parsed = dotenv_values(dotenv_path, interpolate=False)
        except OSError as error:
            raise ReviewerConfigurationError(
                f"cannot read reviewer dotenv {dotenv_path}: {error}"
            ) from error
        if any(value is None for value in parsed.values()):
            raise ReviewerConfigurationError(
                f"reviewer dotenv {dotenv_path} contains a key without a value"
            )
        file_environment = {
            key: value for key, value in parsed.items() if value is not None
        }
    return {**file_environment, **process_environment}


def _load_file_values(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise ReviewerConfigurationError(
            f"reviewer config not found at {config_path}; create it from "
            "the bundled examples/config.json and set provider/model"
        )
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewerConfigurationError(str(error)) from error
    if not isinstance(payload, dict):
        raise ReviewerConfigurationError("config.json must contain an object")
    reviewer = payload.get("reviewer", {})
    if not isinstance(reviewer, dict):
        raise ReviewerConfigurationError("config.json reviewer must be an object")
    unknown_fields = set(reviewer) - {
        "provider",
        "model",
        "api_key_env",
        "options",
    }
    if unknown_fields:
        raise ReviewerConfigurationError(
            f"unknown reviewer settings: {sorted(unknown_fields)}"
        )
    return reviewer


def load_reviewer_config(
    *,
    config_path: Path | None = None,
    provider: str | None = None,
    model: str | None = None,
    api_key_env: str | None = None,
    options: Mapping[str, Any] | None = None,
    environment: Mapping[str, str] | None = None,
    home_directory: Path | None = None,
) -> ReviewerModelConfig:
    """Load explicit, environment, then global reviewer settings."""

    env = os.environ if environment is None else environment
    explicit_pair = (provider, model)
    environment_pair = (
        env.get("AGENT_REVIEW_REVIEWER_PROVIDER"),
        env.get("AGENT_REVIEW_REVIEWER_MODEL"),
    )
    if any(explicit_pair):
        resolved_provider, resolved_model = explicit_pair
        source = "explicit"
        file_values: dict[str, Any] = {}
    elif any(environment_pair):
        resolved_provider, resolved_model = environment_pair
        source = "environment"
        file_values = {}
    else:
        resolved_path = config_path or reviewer_config_path(
            environment=env,
            home_directory=home_directory,
        )
        file_values = _load_file_values(resolved_path)
        file_pair = (file_values.get("provider"), file_values.get("model"))
        resolved_provider, resolved_model = file_pair
        source = f"global config {resolved_path}"
    if not resolved_provider or not resolved_model:
        raise ReviewerConfigurationError(
            f"{source} must configure reviewer provider and model together"
        )
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
        raise ReviewerConfigurationError(
            "config.json reviewer.options must be an object"
        )
    merged_options = {**file_options, **(options or {})}
    try:
        return ReviewerModelConfig(
            provider=normalized_provider,
            model=str(resolved_model),
            api_key_env=(
                str(resolved_api_key_env) if resolved_api_key_env is not None else None
            ),
            options=merged_options,
        )
    except ValidationError as error:
        raise ReviewerConfigurationError(str(error)) from error
