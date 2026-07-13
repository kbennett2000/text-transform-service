"""Reusable post-generation validators (DESIGN §6).

Each factory returns a :data:`~tts.registry.Validator`: a callable taking the parsed
output object and returning ``None`` when acceptable, or a reason string when not.
Validators run *after* JSON-schema validation and never mutate the output.

Field access is top-level (``output[field]``). Nested-array paths such as
``mentions[].name`` are not needed until cast-mentions (T5); see
NOTES-FOR-NEXT-CYCLES.md.
"""

from __future__ import annotations

from collections.abc import Sequence

from tts.registry import Validator


def max_chars(field: str, n: int) -> Validator:
    """Fail if ``output[field]`` (a string) exceeds ``n`` characters."""

    def _check(output: dict) -> str | None:
        value = output.get(field)
        if isinstance(value, str) and len(value) > n:
            return f"{field}: {len(value)} chars exceeds max {n}"
        return None

    return _check


def min_chars(field: str, n: int) -> Validator:
    """Fail if ``output[field]`` (a string) is shorter than ``n`` characters."""

    def _check(output: dict) -> str | None:
        value = output.get(field)
        if isinstance(value, str) and len(value) < n:
            return f"{field}: {len(value)} chars below min {n}"
        return None

    return _check


def banned_substrings(field: str, substrings: Sequence[str]) -> Validator:
    """Fail if ``output[field]`` contains any banned substring — kills markdown/URL
    leakage (``**``, ``##``, ``http``, ``` ``` ```) and stray newlines."""

    def _check(output: dict) -> str | None:
        value = output.get(field)
        if isinstance(value, str):
            for sub in substrings:
                if sub in value:
                    return f"{field}: contains banned substring {sub!r}"
        return None

    return _check


def no_empty_strings(field: str) -> Validator:
    """Fail if ``output[field]`` (a list of strings) contains an empty/blank string."""

    def _check(output: dict) -> str | None:
        value = output.get(field)
        if isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, str) and item.strip() == "":
                    return f"{field}[{i}]: empty string"
        return None

    return _check


def word_range(field: str, lo: int, hi: int) -> Validator:
    """Fail if ``output[field]`` (a string) has a whitespace word count outside
    ``[lo, hi]``."""

    def _check(output: dict) -> str | None:
        value = output.get(field)
        if isinstance(value, str):
            count = len(value.split())
            if count < lo or count > hi:
                return f"{field}: {count} words outside range [{lo}, {hi}]"
        return None

    return _check
