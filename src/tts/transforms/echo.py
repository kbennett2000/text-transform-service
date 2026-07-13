"""Dev-only `echo` transform.

Not a production transform — it exists to prove the pipeline plumbing end-to-end
(route -> pipeline -> LLM client -> parse/validate -> meta) without a real model. It is
registered only when ``TTS_ENV=dev`` (see :func:`tts.transforms.register_all`).

Bound to ``qwen3:0.6b`` (the fast test model) so cycle T3's GPU smoke can exercise it;
under FakeLLM the binding is never actually called.
"""

from __future__ import annotations

from tts.registry import Transform

_TEMPLATE = '''SYSTEM: {common framing}
You echo back the first sentence of the input text.

USER:
Text:
"""
{{ text }}
"""
Return JSON {"echo": "<the first sentence of the text, verbatim>"}.'''

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["echo"],
    "properties": {"echo": {"type": "string"}},
}


def build_echo() -> Transform:
    """Construct the echo transform."""
    return Transform(
        name="echo",
        version="0.1.0",
        template=_TEMPLATE,
        model="qwen3:0.6b",
        options_schema={},
        output_schema=_OUTPUT_SCHEMA,
    )
