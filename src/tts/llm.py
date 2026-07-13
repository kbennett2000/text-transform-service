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

import httpx


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


class LLMBackendError(Exception):
    """The generation backend was unreachable, errored, or returned an unusable body.

    Raised by :class:`OllamaClient`. The pipeline catches it around the ``chat`` call and
    maps it to ``503 model_unavailable`` (DESIGN §4). Deliberately free of any HTTP-taxonomy
    knowledge — mapping to the §4 codes lives in the pipeline, not in the client.
    """

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}


def _model_names(payload: object) -> set[str]:
    """Extract model names from an ``/api/tags`` or ``/api/ps`` body, defensively."""
    if not isinstance(payload, dict):
        return set()
    models = payload.get("models")
    if not isinstance(models, list):
        return set()
    names: set[str] = set()
    for entry in models:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str) and name:
                names.add(name)
    return names


def _split_messages(messages: list[dict]) -> tuple[str, str]:
    """Collapse chat messages into Ollama ``/api/generate``'s ``system`` + ``prompt`` fields.

    ``render_messages`` yields exactly ``[{system}, {user}]``, but we tolerate any mix by
    joining all system-role and all user-role contents in order.
    """
    system = "\n\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
    prompt = "\n\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
    return system.strip(), prompt.strip()


class OllamaClient:
    """:class:`LLMClient` backed by Ollama (DESIGN §5, ADR-0002).

    Uses ``POST /api/generate`` — **not** the ``/api/chat`` written in DESIGN §5 — because on
    the build box's Ollama (0.30.7) ``/api/chat`` silently ignores the ``format`` field, so
    schema-constrained decoding (the ADR-0002 guarantee) only holds via ``/api/generate``. See
    ``docs/models.md``. The rendered system/user messages map to ``generate``'s ``system`` and
    ``prompt`` fields; ``format`` (iff a non-empty schema), top-level ``think``, ``keep_alive``,
    and ``options: {temperature, top_p, num_predict}`` are passed identically.

    The per-request model tag arrives inside ``params["model"]`` (the protocol's ``chat``
    signature has no model argument, and one shared client serves every transform's binding).
    """

    GENERATE_TIMEOUT_S = 120.0  # DESIGN §5: cold model load can take tens of seconds.
    PROBE_TIMEOUT_S = 5.0  # /api/tags, /api/ps — cheap metadata calls.

    def __init__(self, base_url: str, keep_alive: str, timeout_s: float | None = None):
        self._base_url = base_url
        self._keep_alive = keep_alive
        self._timeout_s = timeout_s if timeout_s is not None else self.GENERATE_TIMEOUT_S

    async def chat(self, messages: list[dict], format_schema: dict, params: dict) -> str:
        """Generate against Ollama and return the raw model text (the pipeline parses JSON)."""
        system, prompt = _split_messages(messages)
        body: dict = {
            "model": params["model"],
            "prompt": prompt,
            "stream": False,
            "think": params.get("think", False),
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": params["temperature"],
                "top_p": params["top_p"],
                "num_predict": params["num_predict"],
            },
        }
        if system:
            body["system"] = system
        if format_schema:
            # Grammar-constrained decoding. Omitted for empty schemas — Ollama rejects `{}`.
            body["format"] = format_schema

        data = await self._post_json("/api/generate", body, self._timeout_s)
        response = data.get("response")
        if not isinstance(response, str):
            raise LLMBackendError(
                "ollama /api/generate response missing 'response' text",
                {"keys": sorted(data) if isinstance(data, dict) else None},
            )
        return response

    async def list_tags(self) -> set[str]:
        """Model tags Ollama has pulled (``/api/tags``) — used by the startup check."""
        data = await self._get_json("/api/tags", self.PROBE_TIMEOUT_S)
        return _model_names(data)

    async def list_loaded(self) -> list[str]:
        """Currently-loaded model names (``/api/ps``) — used by the unload endpoint."""
        data = await self._get_json("/api/ps", self.PROBE_TIMEOUT_S)
        return sorted(_model_names(data))

    async def unload(self, model: str) -> None:
        """Unload ``model`` from VRAM via a ``keep_alive: 0`` generate (DESIGN §4)."""
        body = {"model": model, "prompt": "", "stream": False, "keep_alive": 0}
        await self._post_json("/api/generate", body, self.PROBE_TIMEOUT_S)

    async def _post_json(self, path: str, body: dict, timeout_s: float) -> dict:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=timeout_s) as client:
                resp = await client.post(path, json=body)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise LLMBackendError(f"ollama POST {path} failed: {exc}", {"error": str(exc)}) from exc
        except ValueError as exc:  # unparseable JSON body
            raise LLMBackendError(
                f"ollama POST {path} returned an unparseable body: {exc}"
            ) from exc

    async def _get_json(self, path: str, timeout_s: float) -> dict:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=timeout_s) as client:
                resp = await client.get(path)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise LLMBackendError(f"ollama GET {path} failed: {exc}", {"error": str(exc)}) from exc
        except ValueError as exc:
            raise LLMBackendError(f"ollama GET {path} returned an unparseable body: {exc}") from exc
