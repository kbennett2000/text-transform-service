"""`cast-canonicalize` transform (DESIGN §7.3) — Scriptorium P2.

Called once per major character. The evidence rides in ``options`` (the ``text`` input is
normally empty); the transform composes the collected verbatim descriptors into one
paintable canonical visual description an illustrator can work from, choosing plain
era-appropriate defaults where the evidence is silent. Schema, options schema, template,
budget, and validator are verbatim from DESIGN §7.3.

Binding: §7.3 names ``qwen3:8b``, absent on the box; this transform binds the
human-approved T3 rebind ``qwen3.5:9b`` (see ``docs/models.md`` and
NOTES-FOR-NEXT-CYCLES.md). That is the only deviation from §7.3's verbatim definition.
"""

from __future__ import annotations

from tts.registry import Transform
from tts.validators import banned_substrings

# SYSTEM/USER template verbatim from DESIGN §7.3. render_messages passes `options` into the
# Jinja render, so options.name/aliases/era/genre/descriptors resolve here; {common framing}
# is substituted and the first USER: marker splits system from user. Two of §7.3's Jinja
# control-flow lines exceed the 100-char line limit; they are kept byte-verbatim by joining
# adjacent string literals (no newline is introduced at the join), matching pipeline.py's
# COMMON_FRAMING style.
_TEMPLATE = (
    "SYSTEM: {common framing}\n"
    "You write canonical VISUAL descriptions of book characters for an illustrator.\n"
    "\n"
    "USER:\n"
    "Character: {{ options.name }}{% if options.aliases %} (also called: "
    '{{ options.aliases | join(", ") }}){% endif %}\n'
    'Era/setting: {{ options.era | default("unspecified") }}. '
    'Genre: {{ options.genre | default("unspecified") }}.\n'
    "Evidence — verbatim descriptors collected from the text:\n"
    '{% for d in options.descriptors %}- "{{ d }}"\n'
    "{% endfor %}\n"
    "Write:\n"
    '- "visual_description": 2–4 sentences a painter could work from — apparent age,\n'
    "  build, hair, face, characteristic clothing. Use ONLY the evidence; where the\n"
    "  evidence is silent, choose ONE plain era-appropriate default rather than\n"
    "  something distinctive. No personality, no plot, no names of other characters.\n"
    '- "one_line": the same person in ≤20 words (used inside image prompts).\n'
    '- "tags": 3–8 short visual tags ("grey beard", "red cloak").\n'
    "Return JSON."
)

_OPTIONS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "descriptors"],
    "properties": {
        "name": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "descriptors": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
        "era": {"type": "string"},
        "genre": {"type": "string"},
    },
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["visual_description", "one_line", "tags"],
    "properties": {
        "visual_description": {"type": "string", "minLength": 80, "maxLength": 700},
        "one_line": {"type": "string", "minLength": 15, "maxLength": 160},
        "tags": {
            "type": "array",
            "items": {"type": "string", "maxLength": 30},
            "maxItems": 8,
        },
    },
}


def build_cast_canonicalize() -> Transform:
    """Construct the ``cast-canonicalize`` transform (DESIGN §7.3)."""
    return Transform(
        name="cast-canonicalize",
        version="0.1.0",
        template=_TEMPLATE,
        model="qwen3.5:9b",  # §7.3 says qwen3:8b (absent); rebound in T3, see docs/models.md
        temperature=0.5,  # mild creativity for gap-filling defaults
        num_predict=400,
        input_budget=1200,
        over_budget="truncate",
        truncation_strategy="head",  # applies to `text`, which is normally empty
        options_schema=_OPTIONS_SCHEMA,
        output_schema=_OUTPUT_SCHEMA,
        validators=(
            banned_substrings(
                "visual_description", ["**", "\n\n", "personality", "brave", "kind"]
            ),
        ),
    )
