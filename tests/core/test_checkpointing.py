from enum import StrEnum
from typing import Any

import pytest
from pydantic import BaseModel

import rubber_ducky.core.checkpointing as checkpointing_module
import rubber_ducky.core.lifecycle as lifecycle_module
import rubber_ducky.core.models as models_module
from rubber_ducky.core.checkpointing import (
    review_checkpoint_serializer,
    review_checkpoint_types,
)
from rubber_ducky.core.lifecycle import start_review
from rubber_ducky.core.models import ReviewRequestBase


class ExtensionState(BaseModel):
    value: str


class UnregisteredState(BaseModel):
    value: str


def test_serializer_factory_accepts_explicit_extension_types() -> None:
    serializer = review_checkpoint_serializer(additional_types=(ExtensionState,))
    original = ExtensionState(value="extension")

    restored = serializer.loads_typed(serializer.dumps_typed(original))

    assert restored == original


def test_serializer_factory_covers_default_review_state() -> None:
    serializer = review_checkpoint_serializer()
    original = start_review(
        ReviewRequestBase(
            task_id="AR-4",
            title="Checkpoint review",
            proposed_solution="Use one serializer factory.",
        )
    )

    restored = serializer.loads_typed(serializer.dumps_typed(original))

    assert restored == original


def test_serializer_factory_does_not_restore_unregistered_type() -> None:
    serializer = review_checkpoint_serializer()
    original = UnregisteredState(value="untrusted")

    restored = serializer.loads_typed(serializer.dumps_typed(original))

    assert not isinstance(restored, UnregisteredState)


def test_every_domain_checkpoint_type_is_registered() -> None:
    defined_domain_types = {
        value
        for module in (models_module, lifecycle_module)
        for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and issubclass(value, (BaseModel, StrEnum))
    }

    assert defined_domain_types <= set(review_checkpoint_types())


def test_serializer_factory_always_passes_explicit_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    def capture_serializer(**kwargs: Any) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        checkpointing_module,
        "JsonPlusSerializer",
        capture_serializer,
    )

    serializer = checkpointing_module.review_checkpoint_serializer()

    assert serializer is sentinel
    assert captured == {"allowed_msgpack_modules": review_checkpoint_types()}
