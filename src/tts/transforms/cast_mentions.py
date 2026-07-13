"""`cast-mentions` transform (DESIGN §7.2) — Scriptorium P1.

Called once per logical page (any order, parallel-safe). Extracts who is mentioned on
the page and their **verbatim** physical descriptors; the caller reduces mentions across
pages downstream. Schema, template, budget, and validator are verbatim from DESIGN §7.2.

Budget is ``reject`` (not ``truncate``): a page over the 1600 est-token budget is a
paginator bug upstream, so we fail loudly with 413 rather than silently drop text.

Binding: §7.2 names ``qwen3:8b``, absent on the box; this transform binds the
human-approved T3 rebind ``qwen3.5:9b`` (same weight class — see ``docs/models.md`` and
NOTES-FOR-NEXT-CYCLES.md). That is the only deviation from §7.2's verbatim definition.
"""

from __future__ import annotations

from tts.registry import Transform
from tts.validators import no_empty_strings

# SYSTEM/USER template verbatim from DESIGN §7.2. render_messages splits on the first
# USER: marker and substitutes {common framing}; {{ text }} is the (rejected-if-over-budget)
# page text.
_TEMPLATE = '''SYSTEM: {common framing}
You extract character mentions from one page of a book.

USER:
Page text:
"""
{{ text }}
"""
List each distinct character (person, or named non-human agent like a ship or
creature acting as a character) mentioned on this page.
Rules:
- "name": the most specific name/label used on THIS page ("the Time Traveller",
  "Weena", "the innkeeper"). Keep the article if the text uses one.
- "aliases": other labels used for the same character on this page.
- "descriptors": verbatim phrases from the text describing physical appearance,
  clothing, age, or bearing. Quote the text's words; do not paraphrase or invent.
  Empty array if none.
- "is_person": false for animals, ships, machines, crowds.
- Skip characters only referenced abstractly ("his late father") unless described.
Return JSON: {"mentions": [...]}'''

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["mentions"],
    "properties": {
        "mentions": {
            "type": "array",
            "maxItems": 15,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "aliases", "descriptors", "is_person"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 60},
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 60},
                        "maxItems": 6,
                    },
                    "descriptors": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 140},
                        "maxItems": 8,
                    },
                    "is_person": {"type": "boolean"},
                },
            },
        }
    },
}


def build_cast_mentions() -> Transform:
    """Construct the ``cast-mentions`` transform (DESIGN §7.2)."""
    return Transform(
        name="cast-mentions",
        version="0.1.0",
        template=_TEMPLATE,
        model="qwen3.5:9b",  # §7.2 says qwen3:8b (absent); rebound in T3, see docs/models.md
        temperature=0.2,
        num_predict=700,
        input_budget=1600,
        over_budget="reject",  # a page over budget is a paginator bug — fail loudly (413)
        options_schema={},
        output_schema=_OUTPUT_SCHEMA,
        validators=(no_empty_strings("mentions[].name"),),
    )
