"""LLM client abstraction (DESIGN §6, ADR-0002 escape hatch).

The pipeline talks to models only through the :class:`LLMClient` protocol, so the
concrete backend (Ollama in T3, or a llama.cpp client later) can be swapped without
touching transforms. This module ships the protocol and the :class:`FakeLLMClient`
used by every non-GPU test; the real ``OllamaClient`` arrives in cycle T3.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Minimal generation interface.

    ``chat`` returns the model's raw text; the pipeline parses and validates the JSON.
    ``format_schema`` is the transform's ``output_schema`` (passed to Ollama's
    ``format`` field for constrained decoding in T3). ``params`` carries
    ``temperature``, ``top_p``, ``num_predict`` and ``think``.
    """

    async def chat(
        self,
        messages: list[dict],
        format_schema: dict,
        params: dict,
    ) -> str: ...


@dataclass
class RecordedCall:
    """One recorded invocation of :meth:`FakeLLMClient.chat`."""

    messages: list[dict]
    format_schema: dict
    params: dict


class FakeLLMClient:
    """Deterministic in-memory LLM for tests.

    Construct with either a list of canned response strings (returned in order) or a
    callable ``(messages, format_schema, params) -> str``. Every call is recorded in
    :attr:`calls` so tests can assert on the messages, the schema passed for
    constrained decoding, and the sampling params (e.g. the retry temperature bump).
    """

    def __init__(self, responses: list[str] | Callable[..., str]):
        self._responses = responses
        self._index = 0
        self.calls: list[RecordedCall] = []

    async def chat(
        self,
        messages: list[dict],
        format_schema: dict,
        params: dict,
    ) -> str:
        self.calls.append(RecordedCall(messages, format_schema, params))
        if callable(self._responses):
            result = self._responses(messages, format_schema, params)
            if inspect.isawaitable(result):
                result = await result
            return result
        if self._index >= len(self._responses):
            # Reuse the last canned response for any extra attempts rather than
            # raising — keeps "always-invalid" retry tests simple.
            return self._responses[-1]
        response = self._responses[self._index]
        self._index += 1
        return response
