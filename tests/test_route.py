"""Tests for POST /v1/transform/{name} (DESIGN §3, §4).

Uses the real app with the FakeLLM injected via dependency override and the dev-only
`echo` transform registered. Covers the route-level error codes (404, 400, 500) and the
happy-path 200 with a full meta block.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tts import app as app_module
from tts.app import app, get_llm_client
from tts.config import Settings
from tts.llm import FakeLLMClient, LLMBackendError
from tts.transforms import register_all


@pytest.fixture
def register_echo(monkeypatch):
    """Register the echo transform under a dev environment."""
    dev = Settings(env="dev")
    monkeypatch.setattr(app_module.app.state, "settings", dev)
    register_all(dev)


def _client_with_llm(fake: FakeLLMClient) -> TestClient:
    app.dependency_overrides[get_llm_client] = lambda: fake
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_unknown_transform_is_404():
    client = _client_with_llm(FakeLLMClient(['{"echo": "x"}']))
    resp = client.post("/v1/transform/does-not-exist", json={"text": "hi"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_transform"


def test_echo_happy_path_returns_200_with_output_and_full_meta(register_echo):
    fake = FakeLLMClient(['{"echo": "First sentence."}'])
    client = _client_with_llm(fake)

    resp = client.post(
        "/v1/transform/echo",
        json={"text": "First sentence. Second sentence.", "options": {}},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["output"] == {"echo": "First sentence."}
    assert set(body["meta"]) == {
        "transform",
        "transform_version",
        "model",
        "input_tokens_est",
        "truncated",
        "attempts",
        "latency_ms",
        "queued_ms",
    }
    assert body["meta"]["transform"] == "echo"


def test_omitted_options_defaults_to_empty(register_echo):
    fake = FakeLLMClient(['{"echo": "x"}'])
    client = _client_with_llm(fake)
    resp = client.post("/v1/transform/echo", json={"text": "hi"})
    assert resp.status_code == 200


def test_malformed_body_is_400_bad_request(register_echo):
    fake = FakeLLMClient(['{"echo": "x"}'])
    client = _client_with_llm(fake)
    # `text` is required and must be a string.
    resp = client.post("/v1/transform/echo", json={"options": {}})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


def test_unexpected_backend_error_is_500_internal(register_echo):
    class Boom(FakeLLMClient):
        async def chat(self, messages, format_schema, params):
            raise RuntimeError("kaboom")

    client = _client_with_llm(Boom(["unused"]))
    resp = client.post("/v1/transform/echo", json={"text": "hi"})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal"


def test_echo_not_registered_when_not_dev(monkeypatch):
    # Prod settings -> register_all registers nothing -> echo is unknown (404).
    prod = Settings(env="prod")
    monkeypatch.setattr(app_module.app.state, "settings", prod)
    register_all(prod)
    client = _client_with_llm(FakeLLMClient(['{"echo": "x"}']))
    resp = client.post("/v1/transform/echo", json={"text": "hi"})
    assert resp.status_code == 404


# ---- POST /v1/models/unload ------------------------------------------------------

class _StubLLM:
    """Stand-in for OllamaClient's unload surface (list_loaded / unload)."""

    def __init__(self, loaded):
        self._loaded = list(loaded)
        self.unloaded: list[str] = []

    async def list_loaded(self):
        return list(self._loaded)

    async def unload(self, model):
        self.unloaded.append(model)
        if model in self._loaded:
            self._loaded.remove(model)


def test_unload_specific_model():
    stub = _StubLLM(["qwen3.5:9b", "qwen3.5:2b"])
    client = _client_with_llm(stub)
    resp = client.post("/v1/models/unload", json={"model": "qwen3.5:9b"})
    assert resp.status_code == 200
    assert resp.json()["unloaded"] == ["qwen3.5:9b"]
    assert stub.unloaded == ["qwen3.5:9b"]


def test_unload_all_when_model_omitted():
    stub = _StubLLM(["qwen3.5:9b", "qwen3.5:2b"])
    client = _client_with_llm(stub)
    resp = client.post("/v1/models/unload", json={})
    assert resp.status_code == 200
    assert set(resp.json()["unloaded"]) == {"qwen3.5:9b", "qwen3.5:2b"}


def test_unload_backend_error_is_503():
    from tts.llm import LLMBackendError

    class _Down(_StubLLM):
        async def list_loaded(self):
            raise LLMBackendError("unreachable")

    client = _client_with_llm(_Down([]))
    resp = client.post("/v1/models/unload", json={"model": "qwen3.5:9b"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "model_unavailable"


# ---- reload-on-demand: eviction self-heals (T14) --------------------------------

class _ReloadStub:
    """Full backend surface (unload + generation) with tracked residency.

    ``chat`` raises ``LLMBackendError`` when the target model isn't resident — the
    condition that produced the interleaved ``model_unavailable`` under load. ``unload``
    evicts; ``ensure_loaded`` reloads. This lets the reload-after-unload path be exercised
    with no live Ollama.
    """

    def __init__(self, model: str):
        self.model = model
        self._resident = {model}
        self.reloads = 0

    async def list_loaded(self):
        return sorted(self._resident)

    async def unload(self, model):
        self._resident.discard(model)

    async def ensure_loaded(self, model):
        if model not in self._resident:
            self._resident.add(model)
            self.reloads += 1

    async def chat(self, messages, format_schema, params):
        if params["model"] not in self._resident:
            raise LLMBackendError("model not resident")
        return '{"echo": "reloaded"}'


def test_transform_after_unload_reloads_and_returns_200(register_echo):
    stub = _ReloadStub("qwen3.5:2b")  # echo's binding
    client = _client_with_llm(stub)

    # An unload evicts the model mid-workload (as a sharing consumer would).
    unload = client.post("/v1/models/unload", json={"model": "qwen3.5:2b"})
    assert unload.status_code == 200
    assert unload.json()["unloaded"] == ["qwen3.5:2b"]

    # A well-behaved caller's next transform must NOT get model_unavailable: the pipeline
    # reloads on demand and the request succeeds.
    resp = client.post("/v1/transform/echo", json={"text": "hi"})
    assert resp.status_code == 200
    assert resp.json()["output"] == {"echo": "reloaded"}
    assert stub.reloads == 1  # it genuinely had to reload


def test_transform_without_prior_unload_does_not_reload(register_echo):
    # Model already resident -> ensure_loaded is a no-op, no spurious warm load.
    stub = _ReloadStub("qwen3.5:2b")
    client = _client_with_llm(stub)
    resp = client.post("/v1/transform/echo", json={"text": "hi"})
    assert resp.status_code == 200
    assert stub.reloads == 0
