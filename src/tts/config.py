"""Service configuration.

Reads the environment variables documented in DESIGN §9. Every var is optional and
has a default; nothing here is secret-by-default (LAN posture). Auth is enabled only
when ``TRANSFORM_API_KEY`` is set (ADR-0003).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    """Resolved service configuration (DESIGN §9)."""

    port: int = 8712
    host: str = "0.0.0.0"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_keep_alive: str = "5m"
    transform_api_key: str | None = None
    queue_wait_s: int = 90
    # Max number of requests allowed to wait for the single generation slot (T14). 0 =
    # unbounded (queue only bounded by queue_wait_s, the pre-T14 behavior). When >0, a
    # request arriving with the queue already full fast-fails 503 busy instead of waiting.
    max_queue_depth: int = 0
    # The model whose residency defines readiness (T14). /ready and /health's `ready`
    # flag report true iff this model is loaded in Ollama. Default is the production
    # working binding (docs/models.md); the service never substitutes models silently.
    primary_model: str = "qwen3.5:9b"
    log_level: str = "INFO"
    # Deployment environment. "dev" enables dev-only transforms (e.g. echo). Not part
    # of the DESIGN §9 table; introduced in T2 for the echo dev gate.
    env: str = "prod"

    @property
    def auth_enabled(self) -> bool:
        """Auth is on iff an API key is configured (ADR-0003)."""
        return self.transform_api_key is not None

    @property
    def is_dev(self) -> bool:
        """True when running in the dev environment (gates dev-only transforms)."""
        return self.env == "dev"

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from the process environment, applying §9 defaults."""
        key = os.getenv("TRANSFORM_API_KEY")
        if key is not None and key.strip() == "":
            key = None
        return cls(
            port=_int_env("TTS_PORT", 8712),
            host=os.getenv("TTS_HOST", "0.0.0.0"),
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "5m"),
            transform_api_key=key,
            queue_wait_s=_int_env("QUEUE_WAIT_S", 90),
            max_queue_depth=_int_env("MAX_QUEUE_DEPTH", 0),
            primary_model=os.getenv("TTS_PRIMARY_MODEL", "qwen3.5:9b"),
            log_level=os.getenv("TTS_LOG_LEVEL", "INFO"),
            env=os.getenv("TTS_ENV", "prod"),
        )


def get_settings() -> Settings:
    """Return settings resolved from the current environment.

    Not cached: tests monkeypatch the environment and call this again. The app
    resolves settings once at startup (see ``app.py``).
    """
    return Settings.from_env()
