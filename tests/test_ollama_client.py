"""Tests for OllamaClient (DESIGN §5) — non-GPU, Ollama mocked with respx.

These assert the HTTP mechanics only: the request body shape sent to Ollama and how responses
and failures are translated. No real model, no wording assertions.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from tts.llm import LLMBackendError, OllamaClient

BASE = "http://ollama.test:11434"

_ECHO_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["echo"],
    "properties": {"echo": {"type": "string"}},
}


def _client() -> OllamaClient:
    return OllamaClient(base_url=BASE, keep_alive="5m")


def _params(**over) -> dict:
    base = {
        "model": "qwen3.5:9b",
        "temperature": 0.3,
        "top_p": 0.8,
        "num_predict": 160,
        "think": False,
    }
    base.update(over)
    return base


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": "You are precise."},
        {"role": "user", "content": "Echo the first sentence. Text: Hello. World."},
    ]


@respx.mock
async def test_chat_posts_generate_with_correct_body_and_returns_response():
    route = respx.post(f"{BASE}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": '{"echo": "Hello."}'})
    )
    out = await _client().chat(_messages(), _ECHO_SCHEMA, _params())

    assert out == '{"echo": "Hello."}'
    assert route.called
    sent = json.loads(route.calls.last.request.content)
    # Top-level fields per DESIGN §5 (adapted to /api/generate — see docs/models.md).
    assert sent["model"] == "qwen3.5:9b"
    assert sent["stream"] is False
    assert sent["think"] is False
    assert sent["keep_alive"] == "5m"
    assert sent["system"] == "You are precise."
    assert sent["prompt"] == "Echo the first sentence. Text: Hello. World."
    assert sent["format"] == _ECHO_SCHEMA
    # Sampling params live under Ollama's `options` sub-object, not top-level.
    assert sent["options"] == {"temperature": 0.3, "top_p": 0.8, "num_predict": 160}
    assert "temperature" not in sent


@respx.mock
async def test_chat_omits_format_for_empty_schema():
    route = respx.post(f"{BASE}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "ok"})
    )
    await _client().chat(_messages(), {}, _params())
    sent = json.loads(route.calls.last.request.content)
    assert "format" not in sent  # Ollama rejects an empty `{}` format.


@respx.mock
async def test_chat_passes_think_true_through():
    route = respx.post(f"{BASE}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "ok"})
    )
    await _client().chat(_messages(), _ECHO_SCHEMA, _params(think=True))
    assert json.loads(route.calls.last.request.content)["think"] is True


@respx.mock
async def test_chat_maps_http_error_to_backend_error():
    respx.post(f"{BASE}/api/generate").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(LLMBackendError):
        await _client().chat(_messages(), _ECHO_SCHEMA, _params())


@respx.mock
async def test_chat_maps_connection_error_to_backend_error():
    respx.post(f"{BASE}/api/generate").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LLMBackendError):
        await _client().chat(_messages(), _ECHO_SCHEMA, _params())


@respx.mock
async def test_chat_missing_response_key_is_backend_error():
    respx.post(f"{BASE}/api/generate").mock(
        return_value=httpx.Response(200, json={"done": True})
    )
    with pytest.raises(LLMBackendError):
        await _client().chat(_messages(), _ECHO_SCHEMA, _params())


@respx.mock
async def test_list_tags_parses_names():
    respx.get(f"{BASE}/api/tags").mock(
        return_value=httpx.Response(
            200, json={"models": [{"name": "qwen3.5:9b"}, {"model": "qwen3.5:2b"}]}
        )
    )
    assert await _client().list_tags() == {"qwen3.5:9b", "qwen3.5:2b"}


@respx.mock
async def test_list_loaded_parses_names():
    respx.get(f"{BASE}/api/ps").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3.5:9b"}]})
    )
    assert await _client().list_loaded() == ["qwen3.5:9b"]


@respx.mock
async def test_unload_posts_keep_alive_zero():
    route = respx.post(f"{BASE}/api/generate").mock(
        return_value=httpx.Response(200, json={"done": True})
    )
    await _client().unload("qwen3.5:9b")
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "qwen3.5:9b"
    assert sent["keep_alive"] == 0


@respx.mock
async def test_list_tags_backend_error_on_unreachable():
    respx.get(f"{BASE}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LLMBackendError):
        await _client().list_tags()
