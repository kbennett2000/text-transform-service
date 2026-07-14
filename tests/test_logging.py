"""Tests for the structured access log + X-Request-Id (DESIGN §9, cycle T7).

One JSON line per ``/v1/*`` request on the ``tts.request`` logger; ``/health`` is excluded
(polled too often) but every response — including ``/health`` — still carries ``X-Request-Id``.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from tts import app as app_module
from tts.app import app, get_llm_client
from tts.config import Settings
from tts.llm import FakeLLMClient


class _Capture(logging.Handler):
    """Collects raw log messages from ``tts.request`` regardless of propagation."""

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def register_echo(monkeypatch):
    dev = Settings(env="dev")
    monkeypatch.setattr(app_module.app.state, "settings", dev)
    from tts.transforms import register_all

    register_all(dev)


@pytest.fixture
def capture_request_log():
    handler = _Capture()
    logger = logging.getLogger("tts.request")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client(fake):
    app.dependency_overrides[get_llm_client] = lambda: fake
    return TestClient(app)


def test_v1_request_emits_one_parseable_json_line(register_echo, capture_request_log):
    client = _client(FakeLLMClient(['{"echo": "First."}']))
    resp = client.post("/v1/transform/echo", json={"text": "First. Second."})
    assert resp.status_code == 200

    assert len(capture_request_log.messages) == 1
    record = json.loads(capture_request_log.messages[0])
    assert record["transform"] == "echo"
    assert record["status"] == 200
    assert "request_id" in record and record["request_id"]
    assert "ts" in record
    # Meta-derived fields from the successful pipeline run (DESIGN §9).
    for field in ("attempts", "input_tokens_est", "truncated", "queued_ms", "latency_ms"):
        assert field in record


def test_response_carries_x_request_id(register_echo, capture_request_log):
    client = _client(FakeLLMClient(['{"echo": "x"}']))
    resp = client.post("/v1/transform/echo", json={"text": "hi"})
    header_id = resp.headers.get("X-Request-Id")
    assert header_id
    # The logged request_id matches the returned header.
    record = json.loads(capture_request_log.messages[0])
    assert record["request_id"] == header_id


def test_error_line_carries_error_code(register_echo, capture_request_log):
    client = _client(FakeLLMClient(['{"echo": "x"}']))
    resp = client.post("/v1/transform/does-not-exist", json={"text": "hi"})
    assert resp.status_code == 404
    record = json.loads(capture_request_log.messages[0])
    assert record["error_code"] == "unknown_transform"
    assert record["status"] == 404


def test_health_is_not_access_logged_but_has_request_id(capture_request_log):
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-Id")
    # /health is intentionally excluded from the structured access log.
    assert capture_request_log.messages == []
