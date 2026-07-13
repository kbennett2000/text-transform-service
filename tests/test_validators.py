"""Tests for the reusable validator library (DESIGN §6)."""

from __future__ import annotations

from tts.validators import (
    banned_substrings,
    max_chars,
    min_chars,
    no_empty_strings,
    word_range,
)


def test_max_chars():
    v = max_chars("prompt", 5)
    assert v({"prompt": "hello"}) is None
    assert "exceeds max" in v({"prompt": "hello!"})


def test_min_chars():
    v = min_chars("prompt", 5)
    assert v({"prompt": "hello"}) is None
    assert "below min" in v({"prompt": "hi"})


def test_banned_substrings():
    v = banned_substrings("prompt", ["**", "http", "\n"])
    assert v({"prompt": "a clean subject prompt"}) is None
    assert "banned substring" in v({"prompt": "see http://x"})
    assert "banned substring" in v({"prompt": "bold **text**"})


def test_no_empty_strings():
    v = no_empty_strings("descriptors")
    assert v({"descriptors": ["grey beard", "red cloak"]}) is None
    assert "empty string" in v({"descriptors": ["ok", "  "]})


def test_word_range():
    v = word_range("prompt", 2, 4)
    assert v({"prompt": "two words"}) is None
    assert "outside range" in v({"prompt": "only"})
    assert "outside range" in v({"prompt": "one two three four five"})


def test_validators_ignore_absent_or_wrong_type_fields():
    # A missing field or a non-matching type is not this validator's job to flag
    # (the JSON schema already enforces presence/type); the validator returns ok.
    assert max_chars("prompt", 3)({}) is None
    assert word_range("prompt", 1, 2)({"prompt": 123}) is None
    assert no_empty_strings("items")({"items": "not-a-list"}) is None
