import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import rubber_ducky.core.reviewer_config as config_module
from rubber_ducky.core.reviewer_config import (
    ProviderDefinition,
    ReviewerConfigurationError,
    ReviewerModelConfig,
    ReviewerModelFactory,
    UnsupportedReviewerProvider,
    load_reviewer_config,
    reviewer_config_path,
    reviewer_environment,
)


def test_reviewer_config_precedence_is_explicit_env_then_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "rubber_ducky"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(
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
        "RUBBER_DUCKY_REVIEWER_PROVIDER": "anthropic",
        "RUBBER_DUCKY_REVIEWER_MODEL": "env-model",
    }

    from_file = load_reviewer_config(config_path=config_file, environment={})
    from_environment = load_reviewer_config(
        config_path=config_file,
        environment=environment,
    )
    explicit = load_reviewer_config(
        config_path=config_file,
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
        provider="openai",
        model="gpt-test",
        environment={"LLM_PROVIDER_KEY": "super-secret-value"},
    )

    assert "super-secret-value" not in config.model_dump_json()
    assert config.audit_metadata() == {
        "provider": "openai",
        "model": "gpt-test",
    }


def test_global_reviewer_config_rejects_unknown_fields(tmp_path: Path) -> None:
    config_dir = tmp_path / "rubber_ducky"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(
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
        load_reviewer_config(config_path=config_file, environment={})


def test_provider_and_model_must_come_from_same_source(tmp_path: Path) -> None:
    config_dir = tmp_path / "rubber_ducky"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(
        json.dumps({"reviewer": {"provider": "openai", "model": "file-model"}}),
        encoding="utf-8",
    )

    with pytest.raises(ReviewerConfigurationError, match="together"):
        load_reviewer_config(
            config_path=config_file,
            environment={"RUBBER_DUCKY_REVIEWER_PROVIDER": "anthropic"},
        )


def test_global_config_path_honors_xdg_then_home(tmp_path: Path) -> None:
    xdg = reviewer_config_path(
        environment={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        home_directory=tmp_path / "home",
    )
    default = reviewer_config_path(
        environment={},
        home_directory=tmp_path / "home",
    )
    relative_xdg = reviewer_config_path(
        environment={"XDG_CONFIG_HOME": "relative"},
        home_directory=tmp_path / "home",
    )

    assert xdg == tmp_path / "xdg" / "rubber_ducky" / "config.json"
    assert default == tmp_path / "home" / ".config" / "rubber_ducky" / "config.json"
    assert relative_xdg == default


def test_loader_reads_xdg_global_config_without_workspace(tmp_path: Path) -> None:
    config_file = tmp_path / "xdg" / "rubber_ducky" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        json.dumps(
            {
                "reviewer": {
                    "provider": "anthropic",
                    "model": "global-model",
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_reviewer_config(
        environment={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
    )

    assert config.provider == "anthropic"
    assert config.model == "global-model"


def test_missing_global_config_has_setup_guidance(tmp_path: Path) -> None:
    with pytest.raises(ReviewerConfigurationError, match="examples/config.json"):
        load_reviewer_config(
            environment={},
            home_directory=tmp_path,
        )


def test_explicit_config_does_not_read_malformed_global_file(tmp_path: Path) -> None:
    malformed = tmp_path / "config.json"
    malformed.write_text("{", encoding="utf-8")

    config = load_reviewer_config(
        config_path=malformed,
        provider="openai",
        model="explicit-model",
        environment={},
    )

    assert config.provider == "openai"
    assert config.model == "explicit-model"


def test_global_dotenv_loads_credentials_with_process_override(tmp_path: Path) -> None:
    config_path = tmp_path / "rubber_ducky" / "config.json"
    config_path.parent.mkdir()
    dotenv_path = config_path.parent / ".env"
    dotenv_path.write_text(
        "LLM_PROVIDER_KEY=file-secret\nSHARED=file-value\n",
        encoding="utf-8",
    )
    dotenv_path.chmod(0o600)

    environment = reviewer_environment(
        config_path=config_path,
        environment={
            "LLM_PROVIDER_KEY": "process-secret",
            "PROCESS_ONLY": "process-value",
        },
    )

    assert environment == {
        "LLM_PROVIDER_KEY": "process-secret",
        "SHARED": "file-value",
        "PROCESS_ONLY": "process-value",
    }


def test_global_dotenv_rejects_group_or_other_permissions(tmp_path: Path) -> None:
    config_path = tmp_path / "rubber_ducky" / "config.json"
    config_path.parent.mkdir()
    dotenv_path = config_path.parent / ".env"
    dotenv_path.write_text("LLM_PROVIDER_KEY=secret\n", encoding="utf-8")
    dotenv_path.chmod(0o644)

    with pytest.raises(ReviewerConfigurationError, match="permissions"):
        reviewer_environment(config_path=config_path, environment={})


def test_dotenv_cannot_supply_provider_or_model_selection(tmp_path: Path) -> None:
    config_path = tmp_path / "rubber_ducky" / "config.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "reviewer": {
                    "provider": "openai",
                    "model": "global-model",
                }
            }
        ),
        encoding="utf-8",
    )
    dotenv_path = config_path.parent / ".env"
    dotenv_path.write_text(
        "RUBBER_DUCKY_REVIEWER_PROVIDER=anthropic\n"
        "RUBBER_DUCKY_REVIEWER_MODEL=dotenv-model\n",
        encoding="utf-8",
    )
    dotenv_path.chmod(0o600)

    config = load_reviewer_config(config_path=config_path, environment={})

    assert config.provider == "openai"
    assert config.model == "global-model"


def test_dotenv_interpolation_is_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "rubber_ducky" / "config.json"
    config_path.parent.mkdir()
    dotenv_path = config_path.parent / ".env"
    dotenv_path.write_text("LLM_PROVIDER_KEY=${SOURCE_KEY}\n", encoding="utf-8")
    dotenv_path.chmod(0o600)

    environment = reviewer_environment(
        config_path=config_path,
        environment={"SOURCE_KEY": "must-not-expand"},
    )

    assert environment["LLM_PROVIDER_KEY"] == "${SOURCE_KEY}"


def test_project_dotenv_is_never_discovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("PROJECT_SECRET=ignored\n", encoding="utf-8")
    global_config = tmp_path / "global" / "config.json"
    global_config.parent.mkdir()
    monkeypatch.chdir(project)

    environment = reviewer_environment(
        config_path=global_config,
        environment={},
    )

    assert "PROJECT_SECRET" not in environment


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


def test_factory_builds_openrouter_with_full_slug_and_generic_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_init_chat_model(**kwargs: object) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(config_module, "init_chat_model", fake_init_chat_model)
    config = ReviewerModelConfig(
        provider="openrouter",
        model="anthropic/claude-sonnet-4.6",
        options={
            "default_headers": {
                "HTTP-Referer": "https://example.com",
                "X-OpenRouter-Title": "Agent Review",
            }
        },
    )

    model = ReviewerModelFactory().create(
        config,
        environment={"LLM_PROVIDER_KEY": "runtime-secret"},
    )

    assert model is sentinel
    assert captured == {
        "model": "anthropic/claude-sonnet-4.6",
        "model_provider": "openrouter",
        "api_key": "runtime-secret",
        "default_headers": {
            "HTTP-Referer": "https://example.com",
            "X-OpenRouter-Title": "Agent Review",
        },
    }
    assert "runtime-secret" not in config.model_dump_json()
    assert config.audit_metadata() == {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4.6",
    }


@pytest.mark.parametrize("provider", ("openai", "openrouter"))
def test_factory_reports_missing_provider_integration(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    def missing_integration(**_kwargs: object) -> object:
        raise ImportError("install langchain-openai")

    monkeypatch.setattr(config_module, "init_chat_model", missing_integration)
    config = ReviewerModelConfig(provider=provider, model="configured-model")

    with pytest.raises(ReviewerConfigurationError, match="integration"):
        ReviewerModelFactory().create(
            config,
            environment={"LLM_PROVIDER_KEY": "runtime-secret"},
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
        ("openai", "LLM_PROVIDER_KEY"),
        ("anthropic", "LLM_PROVIDER_KEY"),
        ("openrouter", "LLM_PROVIDER_KEY"),
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

    with pytest.raises(ReviewerConfigurationError, match="LLM_PROVIDER_KEY"):
        ReviewerModelFactory().create(
            config,
            environment={"LLM_PROVIDER_KEY": "   "},
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
