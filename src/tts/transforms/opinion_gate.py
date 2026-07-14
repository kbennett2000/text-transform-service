"""`opinion-gate` transform (Brickfeed request, Cycle T10) — editorial harm filter.

Input: a JSON array of candidate stories ``[{"id", "title", "summary"}]``. Output: one
``verdict`` per story classifying whether it is acceptable source material for a lighthearted
satirical opinion section — ``eligible``, ``excluded`` (centers tragedy/violence/death/disaster
casualties/victims), or ``uncertain``. This is net-new (no DESIGN §7.x section); it is a
*reconciled* build of the Brickfeed request in ``docs/requests/brickfeed-2026-07.md`` §2. The
request is the consumer's ask; this module + the T10 CYCLE-LOG entry are the binding contract.

CHARTER: this is a safety-relevant classifier, which DESIGN §1 excludes by default. It is
admitted under **ADR-0007** (``docs/adr/0007-safety-classification-exception.md``), which makes
that exclusion conditional. This module satisfies all three ADR-0007 conditions: (1) the output
is a closed enum verdict with an explicit ``uncertain`` value — no free text drives the decision
(``reason`` is explanatory only); (2) the caller fail-closed obligation is documented below; (3)
scope is editorial gating of machine-selected public content with human audit expected of the
consumer — NOT moderation of user-generated content.

CALLER FAIL-CLOSED OBLIGATION (ADR-0007 condition 2, verbatim contract): the service is
fail-loud and implements NO fallback — it never substitutes a default verdict on error. The
caller MUST treat every transport/validation error (any 4xx/5xx), every ``uncertain`` verdict,
and any missing or duplicated ``id`` as the safe outcome (**exclude**). ``verdict`` is the sole
decision field; ``reason`` is never used to drive a decision. See the RESPONSE doc
(``docs/requests/brickfeed-2026-07-RESPONSE.md``) for the consumer-facing statement of this.

Reconciliation — deviations from the request doc (recorded here so the Brickfeed provider cycle
reads these, not the request, as the contract):

1. The ``verdict`` enum gains a third value ``"uncertain"`` (ADR-0007 condition 1). The request
   listed only ``["eligible", "excluded"]`` and baked "if uncertain, exclude" into the prompt
   intent; TTS instead emits ``uncertain`` honestly and leaves the exclude mapping to the caller
   (above), so the service stays fail-loud and never invents a safe default.
2. ``verdict`` gets an explicit ``"type": "string"`` alongside its ``enum`` (house style; the
   request wrote the field as ``enum`` only).
3. ``verdicts`` is bounded ``maxItems: 100`` and ``reason`` gains ``minLength: 1`` (T9
   NOTES-FOR-NEXT-CYCLES guidance for this transform if ever approved).
4. ``over_budget="reject"`` (→ 413) is kept from the request: silently truncating the candidate
   list would drop stories from classification (they would be treated as excluded downstream), so
   a loud reject is safer than a partial batch.

id-completeness (one verdict per input id, each echoed exactly once) is not schema-enforceable —
validators see the output and options, never the raw input text. The caller fail-closed rule
(missing/duplicate id → exclude) covers the gap; the T10 GPU test checks id-set equality.

Binding: like every production transform, this binds the human-approved T3 rebind
``qwen3.5:9b`` (see ``docs/models.md`` and NOTES-FOR-NEXT-CYCLES.md).
"""

from __future__ import annotations

from tts.registry import Transform
from tts.validators import no_empty_strings

# SYSTEM/USER template. render_messages splits on the first USER: marker and substitutes
# {common framing}; {{ text }} is the post-truncation input (the JSON array of candidate
# stories). Every line is kept <=100 chars so ruff E501 stays clean without a split.
_TEMPLATE = '''SYSTEM: {common framing}
You are an editorial gate for a lighthearted satirical opinion section. Given candidate news
stories, you decide whether each is acceptable source material for gentle satire.

USER:
Candidate stories (a JSON array of objects with "id", "title", "summary"):
"""
{{ text }}
"""
Classify EVERY story. Return JSON with a "verdicts" array holding exactly one object per input
story, echoing each "id" exactly once. Each verdict object has:
- "id": the story's id, copied verbatim.
- "verdict": exactly one of:
  - "excluded": the story centers tragedy, violence, death, disaster casualties, or victims,
    or otherwise would be cruel to treat as satire.
  - "eligible": a harmless, lighthearted story that is safe to satirize gently.
  - "uncertain": you genuinely cannot tell whether satire would be harmful. Emit this instead
    of guessing; do NOT default to eligible when unsure.
- "reason": a brief phrase (1-200 characters) explaining the verdict. One line, no markdown.
Judge by the story's center of gravity, not an incidental mention. Return JSON.'''

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "maxItems": 100,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "verdict", "reason"],
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["eligible", "excluded", "uncertain"],
                    },
                    "reason": {"type": "string", "minLength": 1, "maxLength": 200},
                },
            },
        },
    },
}


def build_opinion_gate() -> Transform:
    """Construct the ``opinion-gate`` transform (Brickfeed request §2, reconciled in T10)."""
    return Transform(
        name="opinion-gate",
        version="0.1.0",
        template=_TEMPLATE,
        model="qwen3.5:9b",  # production binding; see docs/models.md (T3 rebind)
        temperature=0.0,  # deterministic classification gate
        num_predict=1024,  # a verdict per candidate story across a batch
        input_budget=1600,
        over_budget="reject",  # 413; never silently drop candidates (request's own choice)
        options_schema={},
        output_schema=_OUTPUT_SCHEMA,
        validators=(
            no_empty_strings("verdicts[].id"),
            no_empty_strings("verdicts[].reason"),  # catch whitespace-only past minLength:1
        ),
    )
