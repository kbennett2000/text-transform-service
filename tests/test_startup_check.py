"""Tests for the startup model-presence check (DESIGN §5) — non-GPU, warn-only."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from tts.llm import LLMBackendError
from tts.startup import warn_missing_models


class _Tags:
    """Minimal client exposing the async ``list_tags`` the check needs."""

    def __init__(self, tags):
        self._tags = set(tags)

    async def list_tags(self):
        return set(self._tags)


class _Down:
    async def list_tags(self):
        raise LLMBackendError("unreachable")


def _registry(*models):
    return {m: SimpleNamespace(model=m) for m in models}


async def test_missing_bound_model_warns(caplog):
    caplog.set_level(logging.WARNING, logger="tts.startup")
    await warn_missing_models(_Tags({"qwen3.5:2b"}), _registry("qwen3.5:9b"))
    assert any("NOT pulled" in r.message for r in caplog.records)
    assert any("qwen3.5:9b" in r.getMessage() for r in caplog.records)


async def test_all_present_does_not_warn(caplog):
    caplog.set_level(logging.WARNING, logger="tts.startup")
    await warn_missing_models(_Tags({"qwen3.5:9b", "qwen3.5:2b"}), _registry("qwen3.5:9b"))
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


async def test_empty_registry_is_noop(caplog):
    caplog.set_level(logging.WARNING, logger="tts.startup")
    await warn_missing_models(_Tags(set()), {})
    assert caplog.records == []


async def test_unreachable_ollama_is_warned_not_raised(caplog):
    caplog.set_level(logging.WARNING, logger="tts.startup")
    # Must not raise even though the backend is down.
    await warn_missing_models(_Down(), _registry("qwen3.5:9b"))
    assert any("Ollama unreachable" in r.getMessage() for r in caplog.records)
