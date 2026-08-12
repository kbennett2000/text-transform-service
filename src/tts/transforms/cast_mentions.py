"""`cast-mentions` transform (DESIGN §7.2) — Scriptorium P1.

Called once per logical page (any order, parallel-safe). Extracts who is mentioned on
the page and their **verbatim** physical descriptors; the caller reduces mentions across
pages downstream.

T15 (v0.2.0) deviation from §7.2's verbatim template: the ``aliases`` rule now forbids pronouns
and other characters' names (an alias must denote the same person). Motivated by downstream
contamination — polluted aliases let a character cross-link into the wrong scene and get the wrong
appearance. The caller (Scriptorium ``reduce_cast``) also filters these deterministically; this
fixes them at source. Schema and budget are unchanged.

T20 (v0.3.0) goes further, after a 239-character book shipped 731 aliases of which ~73% were junk:
aliases must be **proper names**, never role/relational labels ("the old man" was attached to nine
different characters) or vocative phrases ("my dear Alexey"); ``name`` prefers a proper name when
the page offers one, so "the elder" and "Father Zossima" stop reducing to two separate characters;
and same-page cross-name aliases are stripped in ``normalize`` rather than 422'd. Schema unchanged.

Budget is ``reject`` (not ``truncate``): a page over the 1600 est-token budget is a
paginator bug upstream, so we fail loudly with 413 rather than silently drop text.

Binding: §7.2 names ``qwen3:8b``, absent on the box; this transform binds the
human-approved T3 rebind ``qwen3.5:9b`` (same weight class — see ``docs/models.md`` and
NOTES-FOR-NEXT-CYCLES.md). That is the only deviation from §7.2's verbatim definition.
"""

from __future__ import annotations

from tts.registry import Transform
from tts.validators import no_empty_strings


def _drop_contaminated_aliases(output: dict) -> dict:
    """Remove aliases that name a *different* character mentioned on the same page (T20).

    The template has forbidden this since v0.2.0 and the model still does it — a 458-plate book
    shipped "Kalganov" and "Smurov" as aliases of Fyodor Pavlovitch. Within one page the check is
    exact and deterministic (both names are right there in the same output), so clean it rather
    than 422 the page and burn the retry ladder — the same posture as `illustration-prompt`'s
    camera-framing strip. Downstream cross-page contamination stays Scriptorium's job.
    """
    mentions = output.get("mentions")
    if not isinstance(mentions, list):
        return output
    names = {
        str(m.get("name", "")).strip().casefold()
        for m in mentions
        if isinstance(m, dict) and str(m.get("name", "")).strip()
    }
    cleaned = []
    for m in mentions:
        if not isinstance(m, dict) or not isinstance(m.get("aliases"), list):
            cleaned.append(m)
            continue
        own = str(m.get("name", "")).strip().casefold()
        keep = [
            a for a in m["aliases"]
            if not (str(a).strip().casefold() in names and str(a).strip().casefold() != own)
        ]
        cleaned.append({**m, "aliases": keep})
    return {**output, "mentions": cleaned}

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
- "name": if the page gives this character a proper name anywhere, use that proper
  name ("Weena", "Father Zossima") — even where the passage itself says only "the
  elder". Only when the page never names them, use their most specific label ("the
  Time Traveller", "the innkeeper"), keeping the article the text uses.
- "aliases": other PROPER NAMES or name-like forms used for the SAME character on
  this page ("Mitya" for "Dmitri", "Mr. Kalganov" for "Kalganov"). Aliases are
  optional — a missing alias is harmless, a wrong one corrupts the character.
  When in doubt, leave it out. Do NOT include:
  * pronouns (he, she, they, him, his, thou, thee);
  * the name of any OTHER character — an alias must denote this same person;
  * role or relational labels ("the old man", "the boy", "the servant", "brother",
    "mamma", "his father") — these describe a role, not a name, and are shared by
    many characters;
  * phrases addressed to or describing the character ("my dear Alexey",
    "Dmitri's father", "the woman living near the house").
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
        # T15 (0.2.0): aliases rule forbids pronouns / other characters' names.
        # T20 (0.3.0): aliases must be proper names only — role/relational labels and vocative
        # phrases banned outright (they were the bulk of downstream contamination); `name` prefers
        # a proper name when the page has one, so "the elder" and "Father Zossima" stop splitting
        # into two characters; same-page cross-name aliases stripped deterministically.
        version="0.3.0",
        template=_TEMPLATE,
        model="qwen3.5:9b",  # §7.2 says qwen3:8b (absent); rebound in T3, see docs/models.md
        temperature=0.2,
        num_predict=700,
        input_budget=1600,
        over_budget="reject",  # a page over budget is a paginator bug — fail loudly (413)
        options_schema={},
        output_schema=_OUTPUT_SCHEMA,
        normalize=_drop_contaminated_aliases,
        validators=(no_empty_strings("mentions[].name"),),
    )
