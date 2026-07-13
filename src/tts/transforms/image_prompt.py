"""`image-prompt` transform (DESIGN §7.1) — Brickfeed's news workload.

Input: a news story. Output: one neutral, concrete image-generation *subject* prompt
(no style/medium/camera words — those are added caller-side downstream). Schema,
template, budget, and validators are verbatim from DESIGN §7.1.

Binding: §7.1 names ``qwen3:8b``, which is absent on the box; this transform binds the
human-approved T3 rebind ``qwen3.5:9b`` (same weight class — see ``docs/models.md`` and
NOTES-FOR-NEXT-CYCLES.md). That is the only deviation from §7.1's verbatim definition.
"""

from __future__ import annotations

from tts.registry import Transform
from tts.validators import banned_substrings, word_range

# SYSTEM/USER template verbatim from DESIGN §7.1. render_messages splits on the first
# USER: marker and substitutes {common framing}; {{ text }} is the post-truncation input.
_TEMPLATE = '''SYSTEM: {common framing}
You convert a news story into one concise image-generation subject prompt.

USER:
Story:
"""
{{ text }}
"""
Write one subject prompt for a single illustrative image of this story.
Rules:
- Describe one concrete visual scene: subject, action, setting.
- 15–50 words, one line, no lists.
- No style, medium, camera, or artist words (added downstream).
- No text/typography in the scene. No logos.
- Prefer the story's primary subject; if multiple topics, pick the most visual.
Return JSON: {"prompt": "..."}'''

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["prompt"],
    "properties": {"prompt": {"type": "string", "minLength": 30, "maxLength": 400}},
}


def build_image_prompt() -> Transform:
    """Construct the ``image-prompt`` transform (DESIGN §7.1)."""
    return Transform(
        name="image-prompt",
        version="0.1.0",
        template=_TEMPLATE,
        model="qwen3.5:9b",  # §7.1 says qwen3:8b (absent); rebound in T3, see docs/models.md
        temperature=0.4,
        num_predict=160,
        input_budget=3000,
        over_budget="truncate",
        truncation_strategy="lede_first_n",
        options_schema={},
        output_schema=_OUTPUT_SCHEMA,
        validators=(
            banned_substrings("prompt", ["**", "##", "http", "\n"]),
            word_range("prompt", 8, 60),
        ),
    )
