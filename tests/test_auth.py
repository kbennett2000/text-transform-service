"""Tests for the optional shared-secret auth (ADR-0003, cycle T7).

Auth is active only when ``TRANSFORM_API_KEY`` is set. When active, every ``/v1/*`` route
requires ``X-Transform-Key``; ``/health`` is always open. A rejection is a 401
``unauthorized`` in the standard ``{"error": {...}}`` envelope.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tts import app as app_module
from tts.app import app, get_llm_client
from tts.config import Settings
from tts.llm import FakeLLMClient
from tts.transforms import register_all

_KEY = "s3cret"


class _StubLLM:
    async def list_loaded(self):
        return []

    async def unload(self, model):
        return None


@pytest.fixture
def auth_on(monkeypatch):
    """Dev registry (echo available) with auth enabled and a FakeLLM injected."""
    settings = Settings(env="dev", transform_api_key=_KEY)
    monkeypatch.setattr(app_module.app.state, "settings", settings)
    register_all(settings)
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient(['{"echo": "hi"}'])
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def auth_off(monkeypatch):
    """Dev registry with no key set — auth disabled (LAN posture)."""
    settings = Settings(env="dev", transform_api_key=None)
    monkeypatch.setattr(app_module.app.state, "settings", settings)
    register_all(settings)
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient(['{"echo": "hi"}'])
    yield
    app.dependency_overrides.clear()


def test_transform_missing_key_is_401(auth_on):
    client = TestClient(app)
    resp = client.post("/v1/transform/echo", json={"text": "hi"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_transform_wrong_key_is_401(auth_on):
    client = TestClient(app)
    resp = client.post(
        "/v1/transform/echo", json={"text": "hi"}, headers={"X-Transform-Key": "nope"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_transform_correct_key_is_200(auth_on):
    client = TestClient(app)
    resp = client.post(
        "/v1/transform/echo", json={"text": "hi"}, headers={"X-Transform-Key": _KEY}
    )
    assert resp.status_code == 200


def test_listing_requires_key(auth_on):
    client = TestClient(app)
    assert client.get("/v1/transforms").status_code == 401
    assert client.get("/v1/transforms", headers={"X-Transform-Key": _KEY}).status_code == 200


def test_unload_requires_key(auth_on):
    app.dependency_overrides[get_llm_client] = lambda: _StubLLM()
    client = TestClient(app)
    assert client.post("/v1/models/unload", json={}).status_code == 401
    ok = client.post("/v1/models/unload", json={}, headers={"X-Transform-Key": _KEY})
    assert ok.status_code == 200


def test_health_is_always_open(auth_on):
    client = TestClient(app)
    # No header — /health must not be gated.
    assert client.get("/health").status_code == 200


def test_auth_disabled_allows_no_header(auth_off):
    client = TestClient(app)
    resp = client.post("/v1/transform/echo", json={"text": "hi"})
    assert resp.status_code == 200
    assert client.get("/v1/transforms").status_code == 200
