"""Transform registry package (DESIGN §6).

Transform modules live here, one per file. :func:`register_all` holds the explicit
registration list and is called once at app startup; it clears the registry first so it
is idempotent (important for tests). Production transforms (image-prompt, cast-*,
scene-update, illustration-prompt) arrive in cycles T4-T6; T2 ships only the dev-only
``echo`` transform, gated on ``TTS_ENV=dev``.
"""

from __future__ import annotations

from tts.config import Settings
from tts.registry import REGISTRY, register
from tts.transforms.echo import build_echo


def register_all(settings: Settings) -> None:
    """Populate the registry per the current settings.

    Clears the registry first, then registers each transform. ``echo`` is registered
    only in the dev environment.
    """
    REGISTRY.clear()

    if settings.is_dev:
        register(build_echo())
