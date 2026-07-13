"""Tests for the config module (DESIGN §9 env table)."""

from __future__ import annotations

import pytest

from tts.config import Settings

# The full §9 env table: (env var, override value, Settings attribute, expected default).
ENV_VARS = [
    ("TTS_PORT", "9000", "port", 8712, 9000),
    ("TTS_HOST", "127.0.0.1", "host", "0.0.0.0", "127.0.0.1"),
    ("OLLAMA_URL", "http://box:11434", "ollama_url", "http://127.0.0.1:11434", "http://box:11434"),
    ("OLLAMA_KEEP_ALIVE", "10m", "ollama_keep_alive", "5m", "10m"),
    ("TRANSFORM_API_KEY", "s3cret", "transform_api_key", None, "s3cret"),
    ("QUEUE_WAIT_S", "45", "queue_wait_s", 90, 45),
    ("TTS_LOG_LEVEL", "DEBUG", "log_level", "INFO", "DEBUG"),
]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Ensure a clean slate: no §9 vars leak in from the real environment."""
    for name, *_ in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_when_env_unset():
    s = Settings.from_env()
    assert s.port == 8712
    assert s.host == "0.0.0.0"
    assert s.ollama_url == "http://127.0.0.1:11434"
    assert s.ollama_keep_alive == "5m"
    assert s.transform_api_key is None
    assert s.queue_wait_s == 90
    assert s.log_level == "INFO"


@pytest.mark.parametrize("name,override,attr,default,expected", ENV_VARS)
def test_env_override(monkeypatch, name, override, attr, default, expected):
    monkeypatch.setenv(name, override)
    s = Settings.from_env()
    assert getattr(s, attr) == expected


def test_auth_disabled_by_default():
    assert Settings.from_env().auth_enabled is False


def test_auth_enabled_when_key_set(monkeypatch):
    monkeypatch.setenv("TRANSFORM_API_KEY", "s3cret")
    s = Settings.from_env()
    assert s.auth_enabled is True
    assert s.transform_api_key == "s3cret"


def test_blank_api_key_treated_as_unset(monkeypatch):
    monkeypatch.setenv("TRANSFORM_API_KEY", "   ")
    s = Settings.from_env()
    assert s.transform_api_key is None
    assert s.auth_enabled is False


def test_blank_int_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TTS_PORT", "")
    assert Settings.from_env().port == 8712
