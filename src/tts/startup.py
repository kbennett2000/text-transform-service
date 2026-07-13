"""Startup checks (DESIGN §5).

At boot the service compares every transform's bound model against the models Ollama has
actually pulled (``/api/tags``) and logs a **loud warning** for any that are missing. This is
advisory only — a missing model is not fatal at startup (the model may be pulled later, or the
transform may never be called); the actual per-request failure surfaces as ``503
model_unavailable`` when generation is attempted.
"""

from __future__ import annotations

import logging

from tts.llm import LLMBackendError

logger = logging.getLogger("tts.startup")


async def warn_missing_models(client, registry: dict, log: logging.Logger | None = None) -> None:
    """Warn (never raise) about registry-bound models absent from Ollama's pulled tags.

    ``client`` needs an async ``list_tags() -> set[str]``. Ollama being unreachable is itself
    only a warning here — health/generation report that condition through their own paths.
    """
    log = log or logger
    bound = {t.model for t in registry.values()}
    if not bound:
        return

    try:
        available = await client.list_tags()
    except LLMBackendError as exc:
        log.warning("startup model check skipped — Ollama unreachable: %s", exc)
        return

    missing = sorted(bound - available)
    if missing:
        log.warning(
            "STARTUP: bound models NOT pulled in Ollama: %s (pull them or generation will "
            "503). Available: %s",
            missing,
            sorted(available),
        )
    else:
        log.info("startup model check: all %d bound model(s) present in Ollama", len(bound))
