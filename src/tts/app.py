"""FastAPI application.

Exposes ``GET /health`` (DESIGN §4), ``POST /v1/transform/{name}`` backed by the §3 pipeline,
``POST /v1/models/unload`` (T3), and ``GET /v1/transforms`` (T7). The generation backend is the
real :class:`~tts.llm.OllamaClient`, wired to ``app.state.llm`` at startup and injected via the
``get_llm_client`` dependency; tests override that dependency with a FakeLLM (or a stub) to
exercise the pipeline without a live model. As of T7 the ``/v1/*`` routes carry optional
shared-secret auth (``require_api_key``, ADR-0003) and every request is tagged with an
``X-Request-Id`` and logged as one structured JSON line (``log_requests`` middleware, DESIGN §9).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tts import __version__
from tts.concurrency import GenerationGate
from tts.config import Settings, get_settings
from tts.health import is_ready, probe_ollama
from tts.llm import LLMBackendError, OllamaClient
from tts.logging_setup import configure_logging
from tts.pipeline import TransformError, run_transform
from tts.registry import REGISTRY, Transform
from tts.startup import warn_missing_models
from tts.transforms import register_all

logger = logging.getLogger("tts.app")
# Structured per-request access log (DESIGN §9). One JSON line per /v1/* request.
request_logger = logging.getLogger("tts.request")

# Meta fields carried from a successful pipeline run into the access-log line (DESIGN §9).
_LOG_META_FIELDS = ("attempts", "input_tokens_est", "truncated", "queued_ms", "latency_ms")

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
# Install the structured access log + diagnostic handlers (DESIGN §9). Idempotent.
configure_logging(app.state.settings.log_level)
app.state.started_at = time.monotonic()
# Single in-flight generation slot (ADR-0005), bounded by queue depth (T14 / ADR-0008).
# The unload route acquires the same gate so eviction can't race a generation.
app.state.gen_gate = GenerationGate(
    queue_wait_s=app.state.settings.queue_wait_s,
    max_queue_depth=app.state.settings.max_queue_depth,
)
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


def require_api_key(request: Request) -> None:
    """Auth guard for ``/v1/*`` routes (ADR-0003).

    A no-op unless ``TRANSFORM_API_KEY`` is set (``Settings.auth_enabled``); the LAN
    default is unauthenticated. When enabled, the request must carry
    ``X-Transform-Key: <value>`` or the request is rejected. Raising ``TransformError``
    routes through the global handler into the standard ``{"error": {...}}`` envelope
    (401 ``unauthorized``, DESIGN §4). ``/health`` never depends on this.
    """
    settings = _settings()
    if not settings.auth_enabled:
        return
    provided = request.headers.get("X-Transform-Key")
    if provided is None or provided != settings.transform_api_key:
        raise TransformError(401, "unauthorized", "missing or invalid API key")


def _serialize_transform(t: Transform) -> dict:
    """Project a :class:`Transform` to its ``GET /v1/transforms`` shape (DESIGN §4).

    Only the caller-relevant fields: the Jinja ``template`` source and the Python
    ``validators`` are internal and never serialized.
    """
    return {
        "name": t.name,
        "version": t.version,
        "model": t.model,
        "input_budget": t.input_budget,
        "over_budget": t.over_budget,
        "options_schema": t.options_schema,
        "output_schema": t.output_schema,
    }


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Assign a request id, echo it in ``X-Request-Id``, and log ``/v1/*`` requests (DESIGN §9).

    Every response carries ``X-Request-Id`` (uuid4 hex short). One structured JSON line is
    emitted per ``/v1/*`` request on ``tts.request`` — ``/health`` is intentionally excluded
    (it is polled frequently; a line per poll is noise). The transform route stashes its
    result ``meta`` and error code on ``request.state`` for inclusion here.
    """
    request_id = uuid4().hex[:8]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id

    if request.url.path.startswith("/v1/"):
        record: dict = {
            "ts": datetime.now(UTC).isoformat(),
            "request_id": request_id,
            "transform": getattr(request.state, "transform_name", None),
            "status": response.status_code,
        }
        meta = getattr(request.state, "log_meta", None)
        if meta is not None:
            for key in _LOG_META_FIELDS:
                if key in meta:
                    record[key] = meta[key]
        error_code = getattr(request.state, "error_code", None)
        if error_code is not None:
            record["error_code"] = error_code
        request_logger.info(json.dumps(record))

    return response


@app.exception_handler(RequestValidationError)
async def _on_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Malformed request body -> 400 bad_request (DESIGN §4).

    FastAPI's default for body-validation failures is 422, but 422 is reserved in this
    API for generation validation failures. Remap request-shape errors to 400.
    """
    request.state.error_code = "bad_request"
    return _error_response(400, "bad_request", "malformed request body", {"errors": exc.errors()})


@app.exception_handler(TransformError)
async def _on_transform_error(request: Request, exc: TransformError) -> JSONResponse:
    """Serialize a ``TransformError`` raised outside the transform route body (DESIGN §4).

    The transform route catches its own pipeline errors inline (so a genuine bug still
    maps to 500); this handler covers errors raised in dependencies — notably the
    ``require_api_key`` 401 on the listing and unload routes.
    """
    request.state.error_code = exc.code
    return _error_response(exc.status, exc.code, exc.message, exc.detail)


@app.get("/health")
async def health() -> dict:
    """Report service + Ollama health. Never 500s — degradation is data (DESIGN §4).

    ``status`` is ``"ok"`` iff Ollama's ``/api/ps`` answered, else ``"degraded"`` — the
    unchanged §4 contract. As of T14, an additive ``ready`` boolean reports whether the
    primary model is actually resident (true readiness); it does not affect ``status``.
    """
    settings = _settings()
    result = await probe_ollama(settings.ollama_url)
    uptime_s = int(time.monotonic() - app.state.started_at)
    return {
        "status": "ok" if result.reachable else "degraded",
        "ready": is_ready(result, settings.primary_model),
        "ollama_reachable": result.reachable,
        "models_loaded": result.models_loaded,
        "uptime_s": uptime_s,
    }


@app.get("/ready")
async def ready() -> dict:
    """Report true model readiness for serving a transform (T14). Never 500s.

    Distinct from ``/health`` ``status``: ``ready`` is true iff Ollama is reachable AND the
    primary model (``TTS_PRIMARY_MODEL``, default the production working binding) is resident.
    Lets a caller distinguish "up but no model loaded" (e.g. just after a ``/v1/models/unload``)
    from "loaded and able to serve immediately". Unauthenticated, like ``/health``.
    """
    settings = _settings()
    result = await probe_ollama(settings.ollama_url)
    return {
        "ready": is_ready(result, settings.primary_model),
        "ollama_reachable": result.reachable,
        "models_loaded": result.models_loaded,
        "primary_model": settings.primary_model,
        "uptime_s": int(time.monotonic() - app.state.started_at),
    }


@app.get("/v1/transforms", dependencies=[Depends(require_api_key)])
async def list_transforms() -> dict:
    """List every registered transform with its schemas (DESIGN §4).

    Returns ``{"transforms": [...]}`` sorted by name; each entry carries the caller-facing
    fields incl. both JSON Schemas. Respects auth like the other ``/v1/*`` routes.
    """
    return {
        "transforms": [_serialize_transform(REGISTRY[name]) for name in sorted(REGISTRY)],
    }


@app.post("/v1/transform/{name}", dependencies=[Depends(require_api_key)])
async def transform(
    name: str, req: TransformRequest, request: Request, llm=Depends(get_llm_client)
):
    """Run a named transform over the input text (DESIGN §3, §4)."""
    request.state.transform_name = name
    transform_def = REGISTRY.get(name)
    if transform_def is None:
        request.state.error_code = "unknown_transform"
        return _error_response(404, "unknown_transform", f"unknown transform: {name!r}")

    if llm is None:
        # Defensive: app state was never wired with a backend. Machine-distinguishable,
        # like a model-unavailable condition.
        request.state.error_code = "model_unavailable"
        return _error_response(503, "model_unavailable", "no generation backend configured")

    try:
        result = await run_transform(
            transform_def,
            req.text,
            req.options,
            llm,
            app.state.gen_gate,
        )
        request.state.log_meta = result["meta"]
        return result
    except TransformError as exc:
        request.state.error_code = exc.code
        return _error_response(exc.status, exc.code, exc.message, exc.detail)
    except Exception:  # noqa: BLE001 - unexpected bug -> 500 internal (DESIGN §4)
        request.state.error_code = "internal"
        return _error_response(500, "internal", "internal error")


@app.post("/v1/models/unload", dependencies=[Depends(require_api_key)])
async def unload_models(req: UnloadRequest, llm=Depends(get_llm_client)):
    """Unload one or all loaded models from VRAM (DESIGN §4).

    Body ``{"model": "..."}`` targets one model; ``{}`` targets everything Ollama currently has
    loaded (``/api/ps``). Each target is unloaded via a ``keep_alive: 0`` generate, then the set
    still loaded is re-read so the response reports only models confirmed gone. This is the
    endpoint the Scriptorium orchestrator calls before a render phase (GPU-phase exclusivity).
    Respects auth (ADR-0003) like the other ``/v1/*`` routes.

    The eviction is performed while holding the generation slot (T14), so an unload can never
    race an in-flight generation. If the slot can't be acquired within ``QUEUE_WAIT_S`` (a
    generation is running long), the request 503s ``busy`` via the global handler — the caller
    (between phases) simply retries.
    """
    if llm is None:
        return _error_response(503, "model_unavailable", "no generation backend configured")
    try:
        targets = [req.model] if req.model else await llm.list_loaded()
        # Serialize eviction against generation: hold the slot for the unload + confirmation
        # so no generation can reload the model mid-unload (T14).
        async with app.state.gen_gate.slot():
            for model in targets:
                await llm.unload(model)
            # Confirm via /api/ps. Ollama does not drop a model the instant keep_alive:0
            # returns, so poll briefly (bounded) rather than racing a single read.
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
