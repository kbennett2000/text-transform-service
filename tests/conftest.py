"""Shared test fixtures."""

from __future__ import annotations

import pytest

from tts.registry import REGISTRY


@pytest.fixture(autouse=True)
def _clean_registry():
    """Save and restore the global REGISTRY around every test so registration tests
    and route tests don't leak state into each other."""
    saved = dict(REGISTRY)
    REGISTRY.clear()
    try:
        yield
    finally:
        REGISTRY.clear()
        REGISTRY.update(saved)
