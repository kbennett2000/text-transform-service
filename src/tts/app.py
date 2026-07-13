"""FastAPI application.

Exposes ``GET /health`` (DESIGN §4) and, as of cycle T2, ``POST /v1/transform/{name}``
backed by the §3 pipeline. The generation backend is injected via the ``get_llm_client``
dependency; in T2 no real client is wired (``app.state.llm`` is ``None`` — the real
Ollama client arrives in T3), so a live POST returns 503. Tests override the dependency
with a FakeLLM to exercise the whole pipeline. Auth and the transforms listing are T7.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tts import __version__
from tts.config import Settings, get_settings
from tts.health import probe_ollama
from tts.pipeline import TransformError, run_transform
from tts.registry import REGISTRY
from tts.transforms import register_all

app = FastAPI(title="text-transform-service", version=__version__)

# Resolved once at import/startup. Tests that need overrides patch app.state.settings.
app.state.settings = get_settings()
app.state.started_at = time.monotonic()
# Single in-flight generation slot (ADR-0005). The real OllamaClient is wired in T3;
# until then generation requests have no backend (503).
app.state.gen_semaphore = asyncio.Semaphore(1)
app.state.llm = None

# Register transforms per the resolved environment (echo only when TTS_ENV=dev).
register_all(app.state.settings)


class TransformRequest(BaseModel):
    """Body of ``POST /v1/transform/{name}``. Omitted ``options`` == ``{}`` (DESIGN §4)."""

    text: str
    options: dict = {}


def _settings() -> Settings:
    return app.state.settings


def get_llm_client():
    """Generation backend dependency. ``None`` in T2 (real client wired in T3); tests
    override this to inject a FakeLLM."""
    return app.state.llm


def _error_response(
    status: int, code: str, message: str, detail: dict | None = None
) -> JSONResponse:
    body: dict = {"error": {"code": code, "message": message}}
    if detail is not None:
        body["error"]["detail"] = detail
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(RequestValidationError)
async def _on_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Malformed request body -> 400 bad_request (DESIGN §4).

    FastAPI's default for body-validation failures is 422, but 422 is reserved in this
    API for generation validation failures. Remap request-shape errors to 400.
    """
    return _error_response(400, "bad_request", "malformed request body", {"errors": exc.errors()})


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


@app.post("/v1/transform/{name}")
async def transform(name: str, req: TransformRequest, llm=Depends(get_llm_client)):
    """Run a named transform over the input text (DESIGN §3, §4)."""
    transform_def = REGISTRY.get(name)
    if transform_def is None:
        return _error_response(404, "unknown_transform", f"unknown transform: {name!r}")

    if llm is None:
        # No generation backend wired yet (T3). Machine-distinguishable, like a
        # model-unavailable condition.
        return _error_response(503, "model_unavailable", "no generation backend configured")

    settings = _settings()
    try:
        return await run_transform(
            transform_def,
            req.text,
            req.options,
            llm,
            app.state.gen_semaphore,
            settings.queue_wait_s,
        )
    except TransformError as exc:
        return _error_response(exc.status, exc.code, exc.message, exc.detail)
    except Exception:  # noqa: BLE001 - unexpected bug -> 500 internal (DESIGN §4)
        return _error_response(500, "internal", "internal error")
