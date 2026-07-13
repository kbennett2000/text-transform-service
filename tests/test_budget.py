"""Tests for token estimation and truncation strategies (DESIGN §5)."""

from __future__ import annotations

import math

import pytest

from tts.budget import estimate_tokens, head, lede_first_n


@pytest.mark.parametrize(
    "text, words",
    [
        ("", 0),
        ("one", 1),
        ("one two three", 3),
        ("a b c d e f g h i j", 10),
    ],
)
def test_estimate_tokens_is_ceil_words_times_1_35(text, words):
    assert estimate_tokens(text) == math.ceil(words * 1.35)


def _para(word: str, n: int) -> str:
    return " ".join([word] * n)


@pytest.mark.parametrize("strategy", [lede_first_n, head])
def test_under_budget_is_unchanged_and_not_truncated(strategy):
    text = "para one here.\n\npara two here."
    out, truncated = strategy(text, budget=10_000)
    assert out == text
    assert truncated is False


@pytest.mark.parametrize("strategy", [lede_first_n, head])
def test_over_budget_drops_trailing_paragraphs_on_a_boundary(strategy):
    # Three ~20-word paragraphs (~27 est-tokens each). Budget fits only the first two.
    p0, p1, p2 = _para("alpha", 20), _para("bravo", 20), _para("charlie", 20)
    text = f"{p0}\n\n{p1}\n\n{p2}"
    out, truncated = strategy(text, budget=55)

    assert truncated is True
    assert out == f"{p0}\n\n{p1}"
    assert "charlie" not in out
    assert estimate_tokens(out) <= 55


@pytest.mark.parametrize("strategy", [lede_first_n, head])
def test_no_blank_lines_input_is_returned_unchanged(strategy):
    # A single paragraph (no blank line) over budget: there is no boundary to cut on,
    # so it passes through unchanged and is not flagged truncated.
    text = _para("word", 200)
    out, truncated = strategy(text, budget=10)
    assert out == text
    assert truncated is False


@pytest.mark.parametrize("strategy", [lede_first_n, head])
def test_lede_paragraph_always_kept_even_if_over_budget(strategy):
    # First paragraph alone exceeds budget; keep it (can't split mid-paragraph), drop
    # the rest -> truncated.
    p0 = _para("alpha", 100)
    text = f"{p0}\n\n{_para('bravo', 100)}"
    out, truncated = strategy(text, budget=10)
    assert out == p0
    assert truncated is True
