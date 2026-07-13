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
from tts.llm import FakeLLMClient
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
