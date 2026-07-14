"""Reusable post-generation validators (DESIGN §6).

Each factory returns a :data:`~tts.registry.Validator`: a callable taking the parsed
output object and returning ``None`` when acceptable, or a reason string when not.
Validators run *after* JSON-schema validation and never mutate the output.

Field access is top-level (``output[field]``), except :func:`no_empty_strings`, which
also accepts a one-level array-of-objects path (``mentions[].name``) for cast-mentions
(T5, DESIGN §7.2).

Most validators inspect the output alone. A validator that also needs the request
``options`` (e.g. :func:`depicted_subset_of_cast`, which checks output against the caller's
cast list) sets a ``wants_options`` marker on its callable; the pipeline then calls it as
``validator(output, options)``. Such a validator may return a **soft** finding — a reason
string prefixed ``"warn:"`` — which the pipeline records to ``meta.warnings`` without
failing the request (T6, DESIGN §7.5).
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
    """Fail if a string field contains an empty/blank ("" or whitespace-only) value.

    ``field`` is either a **top-level list field** (``"descriptors"`` -> checks each string
    in ``output["descriptors"]``) or a **one-level array-of-objects path**
    (``"mentions[].name"`` -> checks ``item["name"]`` for each object in
    ``output["mentions"]``). The nested form is what ``cast-mentions`` needs (DESIGN §7.2):
    it catches a whitespace-only ``name`` that slips past the schema's ``minLength: 1``.
    Only one ``[].`` level is supported — exactly the catalog's need, nothing more.
    """
    if "[]." in field:
        array_field, sub = field.split("[].", 1)

        def _check(output: dict) -> str | None:
            items = output.get(array_field)
            if isinstance(items, list):
                for i, item in enumerate(items):
                    if isinstance(item, dict):
                        value = item.get(sub)
                        if isinstance(value, str) and value.strip() == "":
                            return f"{array_field}[{i}].{sub}: empty string"
            return None

        return _check

    def _check(output: dict) -> str | None:
        value = output.get(field)
        if isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, str) and item.strip() == "":
                    return f"{field}[{i}]: empty string"
        return None

    return _check


def depicted_subset_of_cast() -> Validator:
    """Soft-warn if ``output["depicted"]`` names anyone not in the caller's ``options["cast"]``.

    This is the DESIGN §7.5 ``depicted ⊆ cast-names-or-empty`` check. It is deliberately a
    **soft** validator: a stray depicted name is a mild grounding drift the caller may want to
    know about, not a reason to fail the whole illustration prompt. The returned reason is
    therefore prefixed ``"warn:"`` so the pipeline records it to ``meta.warnings`` and still
    returns 200. An empty ``depicted`` (subset of anything) never warns.

    Being options-aware, the returned callable sets ``wants_options = True`` so the pipeline
    invokes it as ``validator(output, options)``.
    """

    def _check(output: dict, options: dict) -> str | None:
        cast = options.get("cast", [])
        names = {c["name"] for c in cast if isinstance(c, dict) and "name" in c}
        depicted = output.get("depicted", [])
        if isinstance(depicted, list):
            extra = [d for d in depicted if d not in names]
            if extra:
                return f"warn:depicted not in cast: {extra}"
        return None

    _check.wants_options = True
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
