"""FastAPI application.

Exposes ``GET /health`` (DESIGN §4), ``POST /v1/transform/{name}`` backed by the §3 pipeline,
and (as of cycle T3) ``POST /v1/models/unload``. The generation backend is the real
:class:`~tts.llm.OllamaClient`, wired to ``app.state.llm`` at startup and injected via the
``get_llm_client`` dependency; tests override that dependency with a FakeLLM (or a stub) to
exercise the pipeline without a live model. Auth and the transforms listing are T7.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tts import __version__
from tts.config import Settings, get_settings
from tts.health import probe_ollama
from tts.llm import LLMBackendError, OllamaClient
from tts.pipeline import TransformError, run_transform
from tts.registry import REGISTRY
from tts.startup import warn_missing_models
from tts.transforms import register_all

logger = logging.getLogger("tts.app")

# Unload confirmation: Ollama drops a model from /api/ps shortly after keep_alive:0, not
# instantly. Poll a few times so the response reports models actually confirmed gone.
_UNLOAD_CONFIRM_POLLS = 6
_UNLOAD_CONFIRM_INTERVAL_S = 0.3


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: warn (loudly, non-fatally) about bound models Ollama has not pulled (DESIGN §5).

    Runs only under the ASGI lifespan protocol (real uvicorn, or ``with TestClient(app)``), so
    the plain-``TestClient(app)`` unit tests make no network calls.
    """
    await warn_missing_models(app.state.llm, REGISTRY, logger)
    yield


app = FastAPI(title="text-transform-service", version=__version__, lifespan=lifespan)

# Resolved once at import/startup. Tests that need overrides patch app.state.settings.
app.state.settings = get_settings()
app.state.started_at = time.monotonic()
# Single in-flight generation slot (ADR-0005).
app.state.gen_semaphore = asyncio.Semaphore(1)
# Real generation backend (T3). The constructor opens no connections; tests override the
# get_llm_client dependency to inject a FakeLLM/stub instead of hitting Ollama.
app.state.llm = OllamaClient(
    base_url=app.state.settings.ollama_url,
    keep_alive=app.state.settings.ollama_keep_alive,
)

# Register transforms per the resolved environment (echo only when TTS_ENV=dev).
register_all(app.state.settings)


class TransformRequest(BaseModel):
    """Body of ``POST /v1/transform/{name}``. Omitted ``options`` == ``{}`` (DESIGN §4)."""

    text: str
    options: dict = {}


class UnloadRequest(BaseModel):
    """Body of ``POST /v1/models/unload``. ``{"model": "..."}`` unloads one; ``{}`` unloads all
    currently-loaded models (DESIGN §4)."""

    model: str | None = None


def _settings() -> Settings:
    return app.state.settings


def get_llm_client():
    """Generation backend dependency. Returns the real :class:`OllamaClient` from app state;
    tests override this to inject a FakeLLM or a stub. Defensively ``None``-guarded at the call
    sites in case app state was never wired."""
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
        # Defensive: app state was never wired with a backend. Machine-distinguishable,
        # like a model-unavailable condition.
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


@app.post("/v1/models/unload")
async def unload_models(req: UnloadRequest, llm=Depends(get_llm_client)):
    """Unload one or all loaded models from VRAM (DESIGN §4).

    Body ``{"model": "..."}`` targets one model; ``{}`` targets everything Ollama currently has
    loaded (``/api/ps``). Each target is unloaded via a ``keep_alive: 0`` generate, then the set
    still loaded is re-read so the response reports only models confirmed gone. This is the
    endpoint the Scriptorium orchestrator calls before a render phase (GPU-phase exclusivity).
    Unauthenticated for now (auth lands in T7).
    """
    if llm is None:
        return _error_response(503, "model_unavailable", "no generation backend configured")
    try:
        targets = [req.model] if req.model else await llm.list_loaded()
        for model in targets:
            await llm.unload(model)
        # Confirm via /api/ps. Ollama does not drop a model the instant keep_alive:0 returns,
        # so poll briefly (bounded) rather than racing a single read.
        still_loaded = set(await llm.list_loaded())
        for _ in range(_UNLOAD_CONFIRM_POLLS):
            if not (set(targets) & still_loaded):
                break
            await asyncio.sleep(_UNLOAD_CONFIRM_INTERVAL_S)
            still_loaded = set(await llm.list_loaded())
        unloaded = [m for m in targets if m not in still_loaded]
        return {"unloaded": unloaded}
    except LLMBackendError as exc:
        return _error_response(503, "model_unavailable", "ollama unload failed", exc.detail or None)
