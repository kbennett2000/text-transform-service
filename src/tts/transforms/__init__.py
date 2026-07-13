"""Transform registry package (DESIGN §6).

Transform modules live here, one per file. :func:`register_all` holds the explicit
registration list and is called once at app startup; it clears the registry first so it
is idempotent (important for tests). ``image-prompt`` (T4) is the first production
transform; the remaining production transforms (cast-*, scene-update,
illustration-prompt) arrive in cycles T5-T6. The dev-only ``echo`` transform is gated on
``TTS_ENV=dev``.
"""

from __future__ import annotations

from tts.config import Settings
from tts.registry import REGISTRY, register
from tts.transforms.echo import build_echo
from tts.transforms.image_prompt import build_image_prompt


def register_all(settings: Settings) -> None:
    """Populate the registry per the current settings.

    Clears the registry first, then registers each transform. Production transforms are
    always registered; ``echo`` is registered only in the dev environment.
    """
    REGISTRY.clear()

    # Production transforms (registered in every environment).
    register(build_image_prompt())

    if settings.is_dev:
        register(build_echo())
