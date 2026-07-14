"""Logging configuration (DESIGN §9).

Two log streams under the ``tts`` namespace:

* ``tts.request`` — the structured access log: exactly one **pure-JSON** line per
  ``/v1/*`` request (the middleware in :mod:`tts.app` builds the record). It does not
  propagate, so nothing prefixes the JSON.
* ``tts`` — human-readable diagnostics (startup warnings, unexpected conditions).

:func:`configure_logging` is idempotent — repeated calls (multiple imports, tests) never
stack duplicate handlers. Level comes from ``TTS_LOG_LEVEL`` (``Settings.log_level``).
"""

from __future__ import annotations

import logging

_REQUEST_MARKER = "_tts_request_handler"
_DIAG_MARKER = "_tts_diag_handler"


def configure_logging(level: str = "INFO") -> None:
    """Install the ``tts.request`` (JSON access) and ``tts`` (diagnostic) handlers.

    Safe to call more than once: handlers already installed by a prior call are detected
    via marker attributes and not re-added.
    """
    level_value = getattr(logging, str(level).upper(), logging.INFO)

    # Access log: one pure-JSON line per request. propagate=False so the diagnostic
    # handler's prefix never wraps the JSON.
    req_logger = logging.getLogger("tts.request")
    req_logger.setLevel(level_value)
    req_logger.propagate = False
    if not any(getattr(h, _REQUEST_MARKER, False) for h in req_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        setattr(handler, _REQUEST_MARKER, True)
        req_logger.addHandler(handler)

    # Diagnostics: startup checks, warnings. Human-readable, timestamped.
    diag_logger = logging.getLogger("tts")
    diag_logger.setLevel(level_value)
    if not any(getattr(h, _DIAG_MARKER, False) for h in diag_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        setattr(handler, _DIAG_MARKER, True)
        diag_logger.addHandler(handler)
