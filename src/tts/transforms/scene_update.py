"""`scene-update` transform (DESIGN §7.4) — Scriptorium P3.

Called once per page **strictly in order**: the caller threads the returned ledger into the
next call's ``prior_ledger``. This single pass produces both the rolling continuity state
(location, time, who is present, carry-notes) and the per-page selection signal
(``visual_salience`` + ``best_visual_beat``). Schema, options schema, template, budget, and
validator are verbatim from DESIGN §7.4.

Budget is ``reject`` (same paginator-bug posture as cast-mentions): a page over budget is a
paginator error, so it fails loudly with 413 rather than being silently truncated.

Binding: §7.4 names ``qwen3:8b``, absent on the box; this transform binds the human-approved
T3 rebind ``qwen3.5:9b`` (see ``docs/models.md`` and NOTES-FOR-NEXT-CYCLES.md). That is the
only deviation from §7.4's verbatim definition.
"""

from __future__ import annotations

from tts.registry import Transform
from tts.validators import banned_substrings

# SYSTEM/USER template verbatim from DESIGN §7.4. render_messages passes `options` into the
# Jinja render, so options.prior_ledger/cast_names/era resolve here; {common framing} is
# substituted and the first USER: marker splits system from user. §7.4's "Known cast" line
# exceeds the 100-char line limit; it is kept byte-verbatim by joining adjacent string
# literals (no newline is introduced at the join), matching cast_canonicalize.py.
_TEMPLATE = (
    "SYSTEM: {common framing}\n"
    "You maintain a rolling scene ledger while reading a book page by page, and you\n"
    "score each page's illustration potential.\n"
    "\n"
    "USER:\n"
    "{% if options.prior_ledger %}Ledger after the previous page:\n"
    "{{ options.prior_ledger | tojson }}\n"
    "{% else %}This is the first page; there is no prior ledger.\n"
    "{% endif %}\n"
    'Known cast (use these exact names in "present" when they match): '
    '{{ options.cast_names | join(", ") }}\n'
    "{% if options.era %}Era/setting: {{ options.era }}.{% endif %}\n"
    "\n"
    "Page text:\n"
    '"""\n'
    "{{ text }}\n"
    '"""\n'
    "Update the ledger for the END of this page:\n"
    "- Carry location/time forward unchanged unless the text moves them.\n"
    '- "scene_changed": true only if the narrative moved to a new location or made a\n'
    "  clear time jump ON this page.\n"
    '- "present": characters physically present at page end (canonical names when known).\n'
    '- "visual_salience": 0–1. High (≥0.7): vivid action, striking imagery, a reveal,\n'
    "  strong atmosphere. Low (≤0.3): abstract discussion, summary, transitional prose.\n"
    '- "best_visual_beat": ONE present-tense sentence describing the single most\n'
    "  illustratable moment on this page, concrete and specific.\n"
    '- "carry_notes": ≤200 chars of continuity facts a future illustrator needs\n'
    "  (injuries, held objects, weather) — cumulative but pruned to what still matters.\n"
    "Return JSON matching the ledger schema exactly."
)

_OPTIONS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["prior_ledger", "cast_names"],
    "properties": {
        "prior_ledger": {"type": ["object", "null"]},
        "cast_names": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
        "era": {"type": "string"},
    },
}

# This object IS the ledger, stored verbatim on the page by the caller.
_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "location",
        "time_of_day",
        "atmosphere",
        "present",
        "scene_changed",
        "visual_salience",
        "best_visual_beat",
        "carry_notes",
    ],
    "properties": {
        "location": {"type": "string", "maxLength": 120},
        "time_of_day": {
            "enum": ["dawn", "morning", "midday", "afternoon", "evening", "night", "unknown"]
        },
        "atmosphere": {"type": "string", "maxLength": 120},
        "present": {
            "type": "array",
            "items": {"type": "string", "maxLength": 60},
            "maxItems": 12,
        },
        "scene_changed": {"type": "boolean"},
        "visual_salience": {"type": "number", "minimum": 0, "maximum": 1},
        "best_visual_beat": {"type": "string", "minLength": 15, "maxLength": 220},
        "carry_notes": {"type": "string", "maxLength": 200},
    },
}


def build_scene_update() -> Transform:
    """Construct the ``scene-update`` transform (DESIGN §7.4)."""
    return Transform(
        name="scene-update",
        version="0.1.0",
        template=_TEMPLATE,
        model="qwen3.5:9b",  # §7.4 says qwen3:8b (absent); rebound in T3, see docs/models.md
        temperature=0.2,
        num_predict=500,
        input_budget=1600,
        over_budget="reject",  # a page over budget is a paginator bug — fail loud, never truncate
        options_schema=_OPTIONS_SCHEMA,
        output_schema=_OUTPUT_SCHEMA,
        validators=(banned_substrings("best_visual_beat", ["\n"]),),
    )
