"""`opinion-image-brief` transform (Brickfeed request, Cycle T10) — subject image brief.

Input: a finished opinion piece (title + body) plus subject context (the source article blocks,
or a phrase describing an invented letter's situation). Output: a subject-only ``imagePrompt``
and a one-line ``caption`` depicting the story/letter **subject** — never the author, never the
act of writing or publishing. This is net-new (no DESIGN §7.x section); it is a *reconciled*
build of the Brickfeed request in ``docs/requests/brickfeed-2026-07.md`` §4. The request is the
consumer's ask; this module + the T10 CYCLE-LOG entry are the binding contract.

The ``imagePrompt`` (30-400) and ``caption`` (15-160) bounds are identical to ``story-cover``'s,
so this reuses T9's subject-neutral validator set (per NOTES-FOR-NEXT-CYCLES).

Reconciliation — deviations from the request doc (recorded here so the Brickfeed provider cycle
reads these, not the request, as the contract):

1. ``imagePrompt``/``caption`` are held **subject-neutral** (ADR-0004). The request's example
   outputs bake in whimsy ("tiny top hats"); a comedic *scene* is fine, but style/mood/medium is
   applied caller-side (incl. Brickfeed's toy-brick treatment), never in the transform. The
   template forbids style/medium/artist/camera words (and "cartoon"/"photo"); the
   ``banned_substrings`` + ``word_range`` validators mirror ``image-prompt``/``story-cover``. The
   request's own subject rules (no text/logos/brands; depict the subject, not the author) are
   preserved. Fixtures store *inputs* only, so no styled output leaks into the repo.
2. ``imagePrompt`` word bound is ``word_range(8, 60)`` — the house ``image-prompt`` binding —
   rather than the request preamble's looser "~15-30 words". The template guides "~15-40 words".

Binding: like every production transform, this binds the human-approved T3 rebind
``qwen3.5:9b`` (see ``docs/models.md`` and NOTES-FOR-NEXT-CYCLES.md).
"""

from __future__ import annotations

from tts.registry import Transform
from tts.validators import banned_substrings, word_range

# SYSTEM/USER template. render_messages splits on the first USER: marker and substitutes
# {common framing}; {{ text }} is the post-truncation input (the finished piece + subject
# context). Every line is kept <=100 chars so ruff E501 stays clean without a split.
_TEMPLATE = '''SYSTEM: {common framing}
You read a finished satirical opinion piece and its subject context, then write a single
image brief -- a subject-only image prompt and a matching one-line caption -- for an
illustration of the piece's SUBJECT.

USER:
Piece and subject context:
"""
{{ text }}
"""
Return JSON with exactly these two fields:
- "imagePrompt": one concrete visual scene for a single illustration -- subject, action,
  setting. About 15-40 words, one line. Describe only what is literally depicted.
  - Depict the story's or letter's SUBJECT -- never the author, never the act of writing,
    editing, or publishing (no writers, desks, typewriters, newspapers, or bylines).
  - No style, medium, camera, artist, "cartoon", or "photo" words (added downstream).
  - No text, letters, numbers, signs, logos, speech bubbles, or brand names in the scene.
- "caption": one line describing the same scene as imagePrompt, 15-160 characters. No
  byline, credit, or attribution.
Return JSON.'''

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["imagePrompt", "caption"],
    "properties": {
        "imagePrompt": {"type": "string", "minLength": 30, "maxLength": 400},
        "caption": {"type": "string", "minLength": 15, "maxLength": 160},
    },
}


def build_opinion_image_brief() -> Transform:
    """Construct the ``opinion-image-brief`` transform (Brickfeed request §4, reconciled T10)."""
    return Transform(
        name="opinion-image-brief",
        version="0.1.0",
        template=_TEMPLATE,
        model="qwen3.5:9b",  # production binding; see docs/models.md (T3 rebind)
        temperature=0.4,  # light imagery creativity, matches image-prompt/story-cover
        num_predict=256,  # two short fields (imagePrompt + caption)
        input_budget=3000,
        over_budget="truncate",
        truncation_strategy="head",  # keep the leading piece; trimming the tail is harmless
        options_schema={},
        output_schema=_OUTPUT_SCHEMA,
        validators=(
            banned_substrings("imagePrompt", ["**", "##", "http", "\n"]),
            word_range("imagePrompt", 8, 60),
            banned_substrings("caption", ["**", "##", "http", "\n"]),
        ),
    )
