import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import agent_review.reviewer_config as config_module
from agent_review.reviewer_config import (
    ProviderDefinition,
    ReviewerConfigurationError,
    ReviewerModelConfig,
    ReviewerModelFactory,
    UnsupportedReviewerProvider,
    load_reviewer_config,
)


def test_reviewer_config_precedence_is_explicit_env_then_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "agent_review"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "reviewer": {
                    "provider": "openai",
                    "model": "file-model",
                }
            }
        ),
        encoding="utf-8",
    )
    environment = {
        "AGENT_REVIEW_REVIEWER_PROVIDER": "anthropic",
        "AGENT_REVIEW_REVIEWER_MODEL": "env-model",
    }

    from_file = load_reviewer_config(tmp_path, environment={})
    from_environment = load_reviewer_config(tmp_path, environment=environment)
    explicit = load_reviewer_config(
        tmp_path,
        provider="openai",
        model="explicit-model",
        environment=environment,
    )

    assert from_file.provider == "openai"
    assert from_file.model == "file-model"
    assert from_environment.provider == "anthropic"
    assert from_environment.model == "env-model"
    assert explicit.provider == "openai"
    assert explicit.model == "explicit-model"


def test_reviewer_config_rejects_secret_values_in_options() -> None:
    with pytest.raises(ValidationError, match="secret"):
        ReviewerModelConfig(
            provider="openai",
            model="gpt-test",
            api_key_env="OPENAI_API_KEY",
            options={"api_key": "must-not-be-stored"},
        )


def test_reviewer_config_allows_non_secret_token_count_options() -> None:
    config = ReviewerModelConfig(
        provider="openai",
        model="gpt-test",
        options={"max_tokens": 2048},
    )

    assert config.options == {"max_tokens": 2048}


def test_reviewer_config_never_contains_environment_secret(tmp_path: Path) -> None:
    config = load_reviewer_config(
        tmp_path,
        provider="openai",
        model="gpt-test",
        environment={"OPENAI_API_KEY": "super-secret-value"},
    )

    assert "super-secret-value" not in config.model_dump_json()
    assert config.audit_metadata() == {
        "provider": "openai",
        "model": "gpt-test",
    }


def test_workspace_reviewer_config_rejects_unknown_fields(tmp_path: Path) -> None:
    config_dir = tmp_path / "agent_review"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "reviewer": {
                    "provider": "openai",
                    "model": "gpt-test",
                    "api_key": "must-not-be-accepted",
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown reviewer settings"):
        load_reviewer_config(tmp_path, environment={})


def test_provider_and_model_must_come_from_same_source(tmp_path: Path) -> None:
    config_dir = tmp_path / "agent_review"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"reviewer": {"provider": "openai", "model": "file-model"}}),
        encoding="utf-8",
    )

    with pytest.raises(ReviewerConfigurationError, match="together"):
        load_reviewer_config(
            tmp_path,
            environment={"AGENT_REVIEW_REVIEWER_PROVIDER": "anthropic"},
        )


def test_factory_builds_selected_provider_and_model_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_init_chat_model(**kwargs: object) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(config_module, "init_chat_model", fake_init_chat_model)
    config = ReviewerModelConfig(
        provider="openai",
        model="gpt-configured",
        api_key_env="CUSTOM_OPENAI_KEY",
        options={"temperature": 0},
    )

    model = ReviewerModelFactory().create(
        config,
        environment={"CUSTOM_OPENAI_KEY": "runtime-secret"},
    )

    assert model is sentinel
    assert captured == {
        "model": "gpt-configured",
        "model_provider": "openai",
        "api_key": "runtime-secret",
        "temperature": 0,
    }


def test_factory_reports_missing_provider_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_integration(**_kwargs: object) -> object:
        raise ImportError("install langchain-openai")

    monkeypatch.setattr(config_module, "init_chat_model", missing_integration)
    config = ReviewerModelConfig(provider="openai", model="gpt-configured")

    with pytest.raises(ReviewerConfigurationError, match="integration"):
        ReviewerModelFactory().create(
            config,
            environment={"OPENAI_API_KEY": "runtime-secret"},
        )


def test_factory_rejects_unsupported_provider() -> None:
    config = ReviewerModelConfig(
        provider="unknown",
        model="some-model",
        api_key_env="UNKNOWN_API_KEY",
    )

    with pytest.raises(UnsupportedReviewerProvider, match="unknown"):
        ReviewerModelFactory().create(config, environment={})


def test_factory_supports_explicit_local_provider_extension() -> None:
    sentinel = object()
    captured: list[tuple[str, str | None]] = []

    def build_local(
        config: ReviewerModelConfig,
        credential: str | None,
    ) -> object:
        captured.append((config.model, credential))
        return sentinel

    factory = ReviewerModelFactory(
        {
            "local": ProviderDefinition(
                build_local,
                requires_credential=False,
            )
        }
    )
    config = ReviewerModelConfig(provider="local", model="offline-model")

    model = factory.create(config, environment={})

    assert model is sentinel
    assert captured == [("offline-model", None)]


@pytest.mark.parametrize(
    ("provider", "credential_env"),
    (
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
    ),
)
def test_builtin_providers_require_environment_credential(
    provider: str,
    credential_env: str,
) -> None:
    config = ReviewerModelConfig(provider=provider, model="configured-model")

    with pytest.raises(ReviewerConfigurationError, match=credential_env):
        ReviewerModelFactory().create(config, environment={})


def test_factory_treats_whitespace_credential_as_missing() -> None:
    config = ReviewerModelConfig(provider="openai", model="configured-model")

    with pytest.raises(ReviewerConfigurationError, match="OPENAI_API_KEY"):
        ReviewerModelFactory().create(
            config,
            environment={"OPENAI_API_KEY": "   "},
        )


@pytest.mark.parametrize("secret_key", ("authorization", "session_token"))
def test_reviewer_config_rejects_common_secret_option_names(
    secret_key: str,
) -> None:
    with pytest.raises(ValidationError, match="secret"):
        ReviewerModelConfig(
            provider="openai",
            model="configured-model",
            options={secret_key: "must-not-persist"},
        )
