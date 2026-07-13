"""Token estimation and truncation strategies (DESIGN §5).

Budgets here are *quality* boundaries, not context limits (Qwen3 context is 32k+; our
largest budget is 4k). Ollama exposes no tokenizer endpoint and we deliberately add no
tokenizer dependency, so we estimate from the whitespace word count.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable

# tokens ~= words * this factor. Documented constant, not a measured tokenization
# (DESIGN §5). Whitespace-split words are the input.
TOKENS_PER_WORD = 1.35

# Paragraphs are separated by one or more blank lines (a line that is empty or all
# whitespace).
_PARA_SPLIT = re.compile(r"\n\s*\n")


def estimate_tokens(text: str) -> int:
    """Estimate token count as ``ceil(words * 1.35)`` (DESIGN §5)."""
    words = len(text.split())
    return math.ceil(words * TOKENS_PER_WORD)


def _paragraphs(text: str) -> list[str]:
    return [p for p in _PARA_SPLIT.split(text) if p.strip()]


def _keep_head(text: str, budget: int) -> tuple[str, bool]:
    """Keep paragraph 0, then subsequent paragraphs in order while the running
    estimate stays within ``budget``. Returns ``(text, truncated)``.

    Shared mechanics for both strategies. A single-paragraph (no blank line) input is
    returned unchanged with ``truncated=False`` — there is no paragraph boundary to
    cut on, so we never split mid-paragraph.
    """
    paras = _paragraphs(text)
    if len(paras) <= 1:
        return text, False

    kept = [paras[0]]
    for para in paras[1:]:
        candidate = "\n\n".join(kept + [para])
        if estimate_tokens(candidate) <= budget:
            kept.append(para)
        else:
            break

    truncated = len(kept) < len(paras)
    if not truncated:
        return text, False
    return "\n\n".join(kept), True


def lede_first_n(text: str, budget: int) -> tuple[str, bool]:
    """News strategy: keep the lede paragraph, then following paragraphs to budget
    (inverted pyramid). Returns ``(text, truncated)`` (DESIGN §5)."""
    return _keep_head(text, budget)


def head(text: str, budget: int) -> tuple[str, bool]:
    """Book-page strategy: keep leading paragraphs to budget on a paragraph boundary.
    Identical mechanics to :func:`lede_first_n`, named separately so book-page
    transforms can diverge later without a rename (DESIGN §5). Returns
    ``(text, truncated)``."""
    return _keep_head(text, budget)


# Strategy name -> callable, for the pipeline to dispatch on Transform.truncation_strategy.
STRATEGIES: dict[str, Callable[[str, int], tuple[str, bool]]] = {
    "lede_first_n": lede_first_n,
    "head": head,
}
