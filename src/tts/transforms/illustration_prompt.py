"""`illustration-prompt` transform (DESIGN §7.5) — Scriptorium P5.

Input: a selected page plus its scene ledger and the cast entries for characters present.
Output: one neutral SDXL *subject* prompt for a single illustration of the page's best
visual beat, weaving each depicted character's visual identifiers in (never a bare name),
with the ``depicted`` set and a ``shot`` framing. Style/medium/artist words are caller-side;
their appearance here is drift. Schema, options schema, template, budget, and validators are
verbatim from DESIGN §7.5.

The ``depicted ⊆ cast`` check is a **soft** validator: a stray depicted name is recorded to
``meta.warnings`` (DESIGN's "warn not fail" posture on name sets), not a 422.

Binding: §7.5 names ``qwen3:8b`` default, absent on the box; this transform binds the
human-approved T3 rebind ``qwen3.5:9b`` (see ``docs/models.md`` and NOTES-FOR-NEXT-CYCLES.md).
That is the only deviation from §7.5's verbatim definition. (§7.5 notes ``qwen3:14b`` as a
possible future swap if an M1 blind read shows subject-selection weakness — not this cycle.)
"""

from __future__ import annotations

from tts.registry import Transform
from tts.validators import banned_substrings, depicted_subset_of_cast, word_range

# SYSTEM/USER template verbatim from DESIGN §7.5. render_messages passes `options` into the
# Jinja render, so options.ledger/cast/era and the `{% for c in options.cast %}` loop resolve
# here; {common framing} is substituted and the first USER: marker splits system from user. A
# triple-single-quoted literal is used (as in image_prompt.py) so the embedded `"""` page
# delimiters survive verbatim; no template line exceeds the 100-char limit.
_TEMPLATE = '''SYSTEM: {common framing}
You write image-generation subject prompts for book illustrations.

USER:
Scene ledger for this page:
{{ options.ledger | tojson }}
{% if options.era %}Era/setting: {{ options.era }}.{% endif %}
Characters available (weave their descriptions in IF depicted; never use a bare
name without its description):
{% for c in options.cast %}- {{ c.name }}: {{ c.one_line }}
{% endfor %}
Page text:
"""
{{ text }}
"""
Write ONE subject prompt depicting this page's best visual beat
("{{ options.ledger.best_visual_beat }}") — you may choose a better beat from the
page text if one exists.
Rules:
- One moment, one composition. 30–80 words, one line.
- Ground the scene: setting, time of day, atmosphere from the ledger.
- For each depicted character, include their visual identifiers from the list
  above (condensed), not just their name.
- No style/medium/artist words. No text or lettering in the scene.
- "shot": wide (environment-dominant), medium (figures in setting), close (faces/objects).
- "avoid": up to 6 short negative hints specific to this scene (e.g., "modern
  clothing", "crowds") — omit generic quality terms.
Return JSON.'''

_OPTIONS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ledger", "cast"],
    "properties": {
        "ledger": {"type": "object"},
        "cast": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "required": ["name", "one_line"],
                "properties": {
                    "name": {"type": "string"},
                    "one_line": {"type": "string"},
                },
            },
        },
        "era": {"type": "string"},
    },
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["prompt", "depicted", "shot"],
    "properties": {
        "prompt": {"type": "string", "minLength": 60, "maxLength": 600},
        "depicted": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "shot": {"enum": ["wide", "medium", "close"]},
        "avoid": {
            "type": "array",
            "items": {"type": "string", "maxLength": 40},
            "maxItems": 6,
        },
    },
}


def build_illustration_prompt() -> Transform:
    """Construct the ``illustration-prompt`` transform (DESIGN §7.5)."""
    return Transform(
        name="illustration-prompt",
        version="0.1.0",
        template=_TEMPLATE,
        model="qwen3.5:9b",  # §7.5 says qwen3:8b (absent); rebound in T3, see docs/models.md
        temperature=0.6,
        num_predict=350,
        input_budget=1600,
        over_budget="reject",  # a page over budget is a paginator bug — fail loud, never truncate
        options_schema=_OPTIONS_SCHEMA,
        output_schema=_OUTPUT_SCHEMA,
        validators=(
            word_range("prompt", 20, 90),
            banned_substrings(
                "prompt",
                ["**", "\n", "style of", "photograph", "oil painting", "watercolor", "engraving"],
            ),
            depicted_subset_of_cast(),
        ),
    )
