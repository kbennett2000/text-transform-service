"""`story-cover` transform (Brickfeed request, Cycle T9) — Brickfeed's story cover bundle.

Input: a story's source context (title / publisher / URL). Output: a five-field cover bundle
— an original ``headline``, a short neutral ``description``, a subject-only ``imagePrompt``, an
editorial ``category`` (fixed enum), and a one-line ``caption``. This is net-new (no DESIGN §7.x
section); it is a *reconciled* build of the Brickfeed request in
``docs/requests/brickfeed-2026-07.md`` §1. The request is the consumer's ask; this module +
the T9 CYCLE-LOG entry are the binding contract.

Reconciliation — deviations from the request doc (recorded here per the T9 kickoff so the
Brickfeed follow-up cycle reads these, not the request, as the contract):

1. ``category`` gets an explicit ``"type": "string"`` alongside its ``enum`` (house style;
   the request wrote the field as ``enum`` only).
2. ``imagePrompt``/``caption`` are held **subject-neutral** (ADR-0004). The request's example
   outputs bake in style/mood ("cartoon", "whirls like a top", "jubilant"), and its preamble
   asks for "playful/cartoonish" — but style (incl. Brickfeed's toy-brick treatment) is applied
   caller-side, never in the transform. The template forbids style/medium/artist/camera words
   (and "cartoon"/"photo"); the ``banned_substrings`` + ``word_range`` validators mirror
   ``image-prompt``. The request's own subject rules (no text/logos/brands in the scene) are
   preserved. Fixtures store *inputs* only, so no styled output leaks into the repo.
3. ``imagePrompt`` word bound is ``word_range(8, 60)`` — the house ``image-prompt`` binding —
   rather than the request preamble's looser "~15–30 words". The template guides "~15–40 words".
4. Truncation is a structural **no-op** for this input. ``over_budget="truncate"`` /
   ``truncation_strategy="head"`` cut only on blank-line paragraph boundaries (see
   ``budget.py``); the story-cover input is a single paragraph (title/publisher/URL on
   consecutive lines), so an over-budget input passes through unchanged (``truncated=False``)
   and is never rejected — which matches the request's "truncating the tail of a long title is
   harmless" intent. See NOTES-FOR-NEXT-CYCLES.md.

Binding: like every production transform, this binds the human-approved T3 rebind
``qwen3.5:9b`` (see ``docs/models.md`` and NOTES-FOR-NEXT-CYCLES.md).
"""

from __future__ import annotations

from tts.registry import Transform
from tts.validators import banned_substrings, word_range

# SYSTEM/USER template. render_messages splits on the first USER: marker and substitutes
# {common framing}; {{ text }} is the post-truncation input (the source-context block). Every
# line is kept <=100 chars so ruff E501 stays clean without a split.
_TEMPLATE = '''SYSTEM: {common framing}
You turn a news story's source context into a compact "story cover" bundle: an original
headline, a short neutral description, a subject-only image prompt, an editorial category,
and a one-line caption.

USER:
Source context:
"""
{{ text }}
"""
Return JSON with exactly these five fields:
- "headline": an ORIGINAL rewrite of the story's angle, 10-200 characters, one line. Convey
  the story in your own words; never copy the source title verbatim.
- "description": a neutral 1-3 sentence summary, 40-600 characters. No opinion, no markdown.
- "imagePrompt": one concrete visual scene for a single illustration -- subject, action,
  setting. About 15-40 words, one line. Describe only what is literally depicted.
  - No style, medium, camera, artist, "cartoon", or "photo" words (added downstream).
  - No text, letters, numbers, signs, logos, speech bubbles, or brand names in the scene.
- "category": exactly one of WORLD, POLITICS, BUSINESS, TECHNOLOGY, SCIENCE, SPORTS,
  CULTURE, OPINION.
- "caption": one line describing the same scene as imagePrompt, 15-160 characters. No
  byline, credit, or attribution.
Return JSON.'''

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "description", "imagePrompt", "category", "caption"],
    "properties": {
        "headline": {"type": "string", "minLength": 10, "maxLength": 200},
        "description": {"type": "string", "minLength": 40, "maxLength": 600},
        "imagePrompt": {"type": "string", "minLength": 30, "maxLength": 400},
        "category": {
            "type": "string",
            "enum": [
                "WORLD",
                "POLITICS",
                "BUSINESS",
                "TECHNOLOGY",
                "SCIENCE",
                "SPORTS",
                "CULTURE",
                "OPINION",
            ],
        },
        "caption": {"type": "string", "minLength": 15, "maxLength": 160},
    },
}


def build_story_cover() -> Transform:
    """Construct the ``story-cover`` transform (Brickfeed request §1, reconciled in T9)."""
    return Transform(
        name="story-cover",
        version="0.1.0",
        template=_TEMPLATE,
        model="qwen3.5:9b",  # production binding; see docs/models.md (T3 rebind)
        temperature=0.4,  # light headline/imagePrompt creativity, matches image-prompt
        num_predict=512,  # five fields (headline+description+imagePrompt+category+caption)
        input_budget=1200,
        over_budget="truncate",
        truncation_strategy="head",  # no-op on the single-paragraph input; never rejects
        options_schema={},
        output_schema=_OUTPUT_SCHEMA,
        validators=(
            banned_substrings("imagePrompt", ["**", "##", "http", "\n"]),
            word_range("imagePrompt", 8, 60),
            banned_substrings("headline", ["**", "##", "http", "\n"]),
            banned_substrings("caption", ["**", "##", "http", "\n"]),
            banned_substrings("description", ["**", "##", "http", "\n\n"]),
        ),
    )
