"""Tests for the Transform dataclass and register() (DESIGN §6)."""

from __future__ import annotations

import dataclasses

import pytest

from tts.registry import REGISTRY, Transform, register


def _minimal() -> Transform:
    return Transform(name="t", version="0.1.0", template="", model="m")


def test_transform_defaults_match_design_6():
    t = _minimal()
    assert t.temperature == 0.3
    assert t.top_p == 0.8
    assert t.num_predict == 512
    assert t.think is False
    assert t.input_budget == 3000
    assert t.over_budget == "truncate"
    assert t.truncation_strategy == "head"
    assert t.options_schema == {}
    assert t.output_schema == {}
    assert t.validators == ()
    assert t.retries == 1
    assert t.temp_bump == 0.15


def test_transform_is_frozen():
    t = _minimal()
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.name = "other"  # type: ignore[misc]


def test_register_adds_to_registry():
    t = _minimal()
    returned = register(t)
    assert returned is t
    assert REGISTRY["t"] is t


def test_duplicate_registration_is_a_startup_error():
    register(_minimal())
    with pytest.raises(ValueError, match="already registered"):
        register(_minimal())
