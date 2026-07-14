"""Transform registry package (DESIGN §6).

Transform modules live here, one per file. :func:`register_all` holds the explicit
registration list and is called once at app startup; it clears the registry first so it
is idempotent (important for tests). ``image-prompt`` (T4), the ``cast-*`` pair (T5), and
``scene-update`` + ``illustration-prompt`` (T6) are production transforms — with T6 the
service covers every Scriptorium bake transform (P1/P2/P3/P5). The dev-only ``echo``
transform is gated on ``TTS_ENV=dev``.
"""

from __future__ import annotations

from tts.config import Settings
from tts.registry import REGISTRY, register
from tts.transforms.cast_canonicalize import build_cast_canonicalize
from tts.transforms.cast_mentions import build_cast_mentions
from tts.transforms.echo import build_echo
from tts.transforms.illustration_prompt import build_illustration_prompt
from tts.transforms.image_prompt import build_image_prompt
from tts.transforms.scene_update import build_scene_update
from tts.transforms.story_cover import build_story_cover


def register_all(settings: Settings) -> None:
    """Populate the registry per the current settings.

    Clears the registry first, then registers each transform. Production transforms are
    always registered; ``echo`` is registered only in the dev environment.
    """
    REGISTRY.clear()

    # Production transforms (registered in every environment).
    register(build_image_prompt())
    register(build_cast_mentions())
    register(build_cast_canonicalize())
    register(build_scene_update())
    register(build_illustration_prompt())
    register(build_story_cover())

    if settings.is_dev:
        register(build_echo())
