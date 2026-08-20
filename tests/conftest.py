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


@pytest.fixture(autouse=True)
def _disable_gpu_lease():
    """Keep the whole suite off the real ``/run/gpu-tenant.lock`` (T22, ADR-0009).

    The transform route passes ``app.state.gen_lease`` into ``run_transform``; with the real,
    enabled lease that would open and contend for the production lockfile on the box (and its
    idle timer would call ``list_loaded`` on a FakeLLM that lacks it). Every test runs with a
    disabled passthrough lease instead; ``test_gpu_lock.py`` builds its own lease directly and
    is unaffected."""
    from tts.app import app
    from tts.gpu_lock import GpuLease

    original = getattr(app.state, "gen_lease", None)
    app.state.gen_lease = GpuLease(None, enabled=False)
    try:
        yield
    finally:
        app.state.gen_lease = original
