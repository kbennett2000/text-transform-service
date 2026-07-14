"""Tests for GET /health (DESIGN §4).

Ollama is mocked with respx — no live server required. Both the reachable and
unreachable cases are covered; the endpoint must return 200 and never 500.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from tts import app as app_module
from tts.config import Settings

OLLAMA_URL = "http://ollama.test:11434"


@pytest.fixture
def client(monkeypatch):
    """A TestClient whose app points at the mock Ollama base URL."""
    monkeypatch.setattr(
        app_module.app.state,
        "settings",
        Settings(ollama_url=OLLAMA_URL),
    )
    return TestClient(app_module.app)


@respx.mock
def test_health_ok_when_ollama_reachable(client):
    respx.get(f"{OLLAMA_URL}/api/ps").mock(
        return_value=httpx.Response(
            200,
            json={"models": [{"name": "qwen3:8b", "size": 123}]},
        )
    )
    respx.get(f"{OLLAMA_URL}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
    )

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ollama_reachable"] is True
    assert body["models_loaded"] == ["qwen3:8b"]
    assert isinstance(body["uptime_s"], int)


@respx.mock
def test_health_degraded_when_ollama_unreachable(client):
    respx.get(f"{OLLAMA_URL}/api/ps").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    resp = client.get("/health")

    # The load-bearing assertion: degraded is data, not a 500.
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["ollama_reachable"] is False
    assert body["models_loaded"] == []


@respx.mock
def test_health_degraded_on_ollama_5xx(client):
    respx.get(f"{OLLAMA_URL}/api/ps").mock(return_value=httpx.Response(500))

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


@respx.mock
def test_health_ok_even_if_tags_fails(client):
    """/api/ps defines status; a failing /api/tags must not flip us to degraded."""
    respx.get(f"{OLLAMA_URL}/api/ps").mock(
        return_value=httpx.Response(200, json={"models": [{"model": "qwen3:0.6b"}]})
    )
    respx.get(f"{OLLAMA_URL}/api/tags").mock(
        side_effect=httpx.ConnectError("tags down")
    )

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ollama_reachable"] is True
    assert body["models_loaded"] == ["qwen3:0.6b"]


# ---- readiness: GET /ready and the additive /health `ready` field (T14) -------------

PRIMARY = "qwen3.5:9b"


def _mock_ps(models: list[dict]):
    respx.get(f"{OLLAMA_URL}/api/ps").mock(
        return_value=httpx.Response(200, json={"models": models})
    )
    respx.get(f"{OLLAMA_URL}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": models})
    )


@respx.mock
def test_ready_true_when_primary_model_resident(client):
    _mock_ps([{"name": PRIMARY, "size": 123}])
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["ollama_reachable"] is True
    assert body["primary_model"] == PRIMARY
    assert PRIMARY in body["models_loaded"]


@respx.mock
def test_ready_false_when_no_model_resident(client):
    # Ollama is up (status would be "ok") but nothing is loaded — the exact gap /ready closes.
    _mock_ps([])
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is False
    assert body["ollama_reachable"] is True
    assert body["models_loaded"] == []


@respx.mock
def test_ready_false_when_a_different_model_resident(client):
    _mock_ps([{"name": "qwen3.5:2b"}])
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["ready"] is False


@respx.mock
def test_ready_false_and_never_500_when_ollama_down(client):
    respx.get(f"{OLLAMA_URL}/api/ps").mock(side_effect=httpx.ConnectError("refused"))
    resp = client.get("/ready")
    assert resp.status_code == 200  # never 500, like /health
    body = resp.json()
    assert body["ready"] is False
    assert body["ollama_reachable"] is False


@respx.mock
def test_health_ready_field_is_additive_and_does_not_change_status(client):
    # /health gains `ready` but `status` stays tied to reachability (§4 contract unchanged).
    _mock_ps([{"name": PRIMARY}])
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ready"] is True


@respx.mock
def test_health_status_ok_but_ready_false_when_model_absent(client):
    # The motivating case: up but not ready. status:ok (Ollama answered), ready:false.
    _mock_ps([])
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ready"] is False
