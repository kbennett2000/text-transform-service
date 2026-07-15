"""Ollama health probe for ``GET /health`` (DESIGN §4).

This is intentionally NOT the future generation client (``OllamaClient``, cycle T3).
It is a tiny, side-effect-free reachability check: hit ``/api/ps`` and ``/api/tags``
with a short timeout and translate any failure into "unreachable" data. Health must
never raise — Ollama being down is ``status: "degraded"`` data, not an error.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

# Short probe timeout (DESIGN §4: 3s). Health should fail fast, not hang.
PROBE_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class OllamaHealth:
    """Result of probing Ollama.

    ``reachable`` is true iff ``/api/ps`` answered (that endpoint defines ``status: ok``
    per DESIGN §4). ``models_loaded`` is the currently-loaded model names from ``/api/ps``.
    """

    reachable: bool
    models_loaded: list[str]


async def probe_ollama(base_url: str, timeout_s: float = PROBE_TIMEOUT_S) -> OllamaHealth:
    """Probe Ollama and return health data. Never raises.

    Reachability is determined solely by ``/api/ps`` (the endpoint DESIGN §4 ties
    ``status`` to). ``/api/tags`` is probed opportunistically to warm the startup
    model check; its failure alone does not mark the service degraded.
    """
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_s) as client:
        try:
            resp = await client.get("/api/ps")
            resp.raise_for_status()
            models = _loaded_model_names(resp.json())
        except (httpx.HTTPError, ValueError):
            # Connection refused, timeout, non-2xx, or unparseable body -> unreachable.
            return OllamaHealth(reachable=False, models_loaded=[])

        # /api/ps answered: we are "ok". Touch /api/tags but don't let it flip status.
        try:
            await client.get("/api/tags")
        except httpx.HTTPError:
            pass

        return OllamaHealth(reachable=True, models_loaded=models)


def is_ready(health: OllamaHealth, primary_model: str) -> bool:
    """True iff Ollama is reachable AND the primary model is resident (T14).

    Readiness is distinct from ``status`` (which is ``ok`` whenever ``/api/ps`` answers):
    it distinguishes "up but no model loaded" (e.g. right after a ``/v1/models/unload``)
    from "loaded and able to serve a transform immediately". Used by ``GET /ready`` and
    the additive ``ready`` field on ``/health``.
    """
    return health.reachable and primary_model in health.models_loaded


def _loaded_model_names(ps_body: object) -> list[str]:
    """Extract loaded model names from an ``/api/ps`` JSON body, defensively."""
    if not isinstance(ps_body, dict):
        return []
    models = ps_body.get("models")
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for entry in models:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str) and name:
                names.append(name)
    return names
