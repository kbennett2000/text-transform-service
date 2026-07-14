"""Tests for the reusable validator library (DESIGN §6)."""

from __future__ import annotations

from tts.validators import (
    banned_substrings,
    depicted_subset_of_cast,
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


def test_no_empty_strings_nested_path():
    # The T5 extension: an "<array>[].<sub>" path checks each object's sub-field. This is
    # cast-mentions' no_empty_strings("mentions[].name") — it catches a whitespace-only
    # name that slips past the schema's minLength:1.
    v = no_empty_strings("mentions[].name")
    clean = {"mentions": [{"name": "Weena"}, {"name": "the Time Traveller"}]}
    assert v(clean) is None
    reason = v({"mentions": [{"name": "Weena"}, {"name": " "}]})
    assert reason is not None
    assert "mentions[1].name" in reason and "empty string" in reason
    # Absent/wrong-type array or missing sub-field is not this validator's job to flag.
    assert v({"mentions": "not-a-list"}) is None
    assert v({"mentions": [{"aliases": []}]}) is None
    assert v({}) is None


def test_word_range():
    v = word_range("prompt", 2, 4)
    assert v({"prompt": "two words"}) is None
    assert "outside range" in v({"prompt": "only"})
    assert "outside range" in v({"prompt": "one two three four five"})


def test_depicted_subset_of_cast():
    # The T6 soft validator (DESIGN §7.5): output.depicted must be a subset of the caller's
    # options.cast names, else a `warn:` finding (recorded, never fatal). Options-aware, so
    # it opts in via `wants_options` and is called as v(output, options).
    v = depicted_subset_of_cast()
    assert v.wants_options is True
    options = {"cast": [{"name": "the Time Traveller"}, {"name": "Weena"}]}
    # subset -> no finding
    assert v({"depicted": ["the Time Traveller"]}, options) is None
    # empty depicted is a subset of anything -> no finding
    assert v({"depicted": []}, options) is None
    assert v({}, options) is None
    # a name not in the cast -> a soft warning
    reason = v({"depicted": ["the Morlock"]}, options)
    assert reason is not None
    assert reason.startswith("warn:")
    assert "the Morlock" in reason
    # empty/absent cast -> any named depiction warns
    assert v({"depicted": ["anyone"]}, {"cast": []}).startswith("warn:")
    assert v({"depicted": ["anyone"]}, {}).startswith("warn:")


def test_validators_ignore_absent_or_wrong_type_fields():
    # A missing field or a non-matching type is not this validator's job to flag
    # (the JSON schema already enforces presence/type); the validator returns ok.
    assert max_chars("prompt", 3)({}) is None
    assert word_range("prompt", 1, 2)({"prompt": 123}) is None
    assert no_empty_strings("items")({"items": "not-a-list"}) is None
