"""FastAPI application.

Cycle T1 exposes only ``GET /health`` (DESIGN §4). Transforms, the request pipeline,
auth, and the generation client arrive in later cycles.
"""

from __future__ import annotations

import time

from fastapi import FastAPI

from tts import __version__
from tts.config import Settings, get_settings
from tts.health import probe_ollama

app = FastAPI(title="text-transform-service", version=__version__)

# Resolved once at import/startup. Tests that need overrides patch app.state.settings.
app.state.settings = get_settings()
app.state.started_at = time.monotonic()


def _settings() -> Settings:
    return app.state.settings


@app.get("/health")
async def health() -> dict:
    """Report service + Ollama health. Never 500s — degradation is data (DESIGN §4).

    ``status`` is ``"ok"`` iff Ollama's ``/api/ps`` answered, else ``"degraded"``.
    """
    settings = _settings()
    result = await probe_ollama(settings.ollama_url)
    uptime_s = int(time.monotonic() - app.state.started_at)
    return {
        "status": "ok" if result.reachable else "degraded",
        "ollama_reachable": result.reachable,
        "models_loaded": result.models_loaded,
        "uptime_s": uptime_s,
    }
