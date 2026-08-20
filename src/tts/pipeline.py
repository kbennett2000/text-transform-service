"""The request pipeline (DESIGN §3).

Given a resolved :class:`~tts.registry.Transform`, the input text, and the validated
options, :func:`run_transform` runs the full §3 sequence — options validation, budget
enforcement, prompt rendering, semaphore-serialized generation, JSON parse + schema +
validators, and retry-with-temperature-bump — returning ``{"output", "meta"}`` on
success or raising :class:`TransformError` mapped to the §4 error taxonomy.

The backend is any :class:`~tts.llm.LLMClient`; all non-GPU tests inject a FakeLLM.
The real Ollama client and its ``model_unavailable`` handling arrive in cycle T3.
"""

from __future__ import annotations

import json
import logging
import re
import time
from contextlib import nullcontext
from typing import TYPE_CHECKING

import jsonschema
from jinja2 import Environment

from tts.budget import STRATEGIES, estimate_tokens
from tts.llm import LLMBackendError
from tts.registry import Transform

if TYPE_CHECKING:
    # Runtime import would be circular: concurrency imports TransformError from here.
    # The annotation is a string under `from __future__ import annotations`, so a
    # type-only import suffices.
    from tts.concurrency import GenerationGate
    from tts.gpu_lock import GpuLease

logger = logging.getLogger("tts.pipeline")

# Max chars of raw model output surfaced on a validation failure (422 detail + debug log).
# A truncation-past-context failure emits empty/garbage that a snippet makes diagnosable
# without dumping a multi-KB batch response.
_RAW_SNIPPET_CHARS = 300

# Prepended to every transform's system message (DESIGN §7, verbatim).
COMMON_FRAMING = (
    "You are a precise text-processing function. You return only JSON matching the\n"
    "required schema. You never add commentary, markdown, or fields not in the schema.\n"
    "When evidence is absent, follow the transform's rules for defaults; never invent\n"
    "specific facts not supported by the input."
)

# No autoescape: prompts are plain text, not HTML. tojson/join/default filters (used by
# the §7 transform templates) are Jinja2 built-ins.
_JINJA = Environment(autoescape=False)


class TransformError(Exception):
    """A pipeline failure mapped to the §4 error taxonomy.

    ``status`` is the HTTP status; ``code`` the ``error.code`` string; ``detail`` the
    optional ``error.detail`` object. The route serializes these into the standard
    ``{"error": {...}}`` envelope.
    """

    def __init__(self, status: int, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


def render_messages(template: str, text: str, options: dict) -> list[dict]:
    """Render a transform's Jinja2 ``template`` into chat messages.

    The §7 templates are written ``SYSTEM: {common framing} ... USER: ...``. We render
    the Jinja2 source with ``text`` and ``options``, then split on the first ``USER:``
    marker: the part before becomes the system message (its leading ``SYSTEM:`` stripped
    and the literal ``{common framing}`` token replaced with :data:`COMMON_FRAMING`), the
    part after becomes the user message. A template with no ``USER:`` marker is treated
    as a bare user message with :data:`COMMON_FRAMING` as the system message.
    """
    rendered = _JINJA.from_string(template).render(text=text, options=options)

    marker = "USER:"
    idx = rendered.find(marker)
    if idx == -1:
        system = COMMON_FRAMING
        user = rendered.strip()
    else:
        system_part = rendered[:idx]
        user = rendered[idx + len(marker):].strip()
        system_part = system_part.strip()
        if system_part.startswith("SYSTEM:"):
            system_part = system_part[len("SYSTEM:"):].strip()
        system = system_part.replace("{common framing}", COMMON_FRAMING).strip()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _validate_options(transform: Transform, options: dict) -> None:
    """Validate options against the transform's options_schema; 400 bad_options on fail."""
    if not transform.options_schema:
        return
    try:
        jsonschema.validate(options, transform.options_schema)
    except jsonschema.ValidationError as exc:
        raise TransformError(
            400, "bad_options", "options failed options_schema", {"reason": exc.message}
        ) from exc


def _enforce_budget(transform: Transform, text: str) -> tuple[str, bool]:
    """Enforce the input budget. Returns ``(text, truncated)``.

    Over budget with ``reject`` policy -> 413 over_budget. With ``truncate`` policy,
    apply the named strategy and flag ``truncated``.
    """
    if estimate_tokens(text) <= transform.input_budget:
        return text, False

    if transform.over_budget == "reject":
        raise TransformError(
            413,
            "over_budget",
            "input exceeds budget",
            {"input_tokens_est": estimate_tokens(text), "budget": transform.input_budget},
        )

    strategy = STRATEGIES.get(transform.truncation_strategy)
    if strategy is None:  # pragma: no cover - guards against a bad transform definition
        raise TransformError(
            500, "internal", f"unknown truncation strategy: {transform.truncation_strategy}"
        )
    return strategy(text, transform.input_budget)


# Unpaired UTF-16 surrogates (U+D800–U+DFFF). ``json.loads`` combines valid surrogate
# *pairs* into their true character, so only *lone* halves survive here — and a lone
# surrogate can never be UTF-8 encoded, so it would 500 the whole response on serialization.
_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def _strip_surrogates(value: object) -> object:
    """Recursively drop unpaired surrogate code points from parsed model output.

    LLMs occasionally emit an escaped lone surrogate — e.g. the high half of a
    Mathematical-Bold letter (``\\ud835``) with no matching low half. It parses into a
    Python ``str`` fine but can never be UTF-8 encoded, so serializing the response 500s
    (and, upstream, kills the whole bake). The code point carries no recoverable character,
    so we drop it. Keys are sanitized too — a surrogate anywhere in the structure is fatal.
    """
    if isinstance(value, str):
        return _SURROGATE_RE.sub("", value)
    if isinstance(value, list):
        return [_strip_surrogates(v) for v in value]
    if isinstance(value, dict):
        return {_strip_surrogates(k): _strip_surrogates(v) for k, v in value.items()}
    return value


def _attempt_reason(
    transform: Transform, raw: str, options: dict
) -> tuple[dict | None, str | None, list[str]]:
    """Parse+validate one generation.

    Returns ``(output, None, warnings)`` on success or ``(None, reason, [])`` on a hard
    failure (invalid JSON, schema violation, or a hard validator). A validator reason that
    begins with ``"warn:"`` is a *soft* finding: its remainder is collected into ``warnings``
    and validation continues (it never fails the attempt, never triggers a retry). Only the
    warnings of a successful attempt are meaningful; a hard-failed attempt returns ``[]``.
    """
    try:
        output = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"invalid JSON: {exc}", []

    # Model output can contain lone surrogates that would 500 on response encoding; scrub
    # them before schema/validators/return so the value is always UTF-8 serializable.
    output = _strip_surrogates(output)

    if transform.output_schema:
        try:
            jsonschema.validate(output, transform.output_schema)
        except jsonschema.ValidationError as exc:
            return None, f"schema: {exc.message}", []

    # Optional cleanup (T18): the one place output may be rewritten — scrub tolerated-but-unwanted
    # fragments before the validators see it, so a stubborn phrase is fixed, not 422'd.
    if transform.normalize is not None:
        output = transform.normalize(output)

    warnings: list[str] = []
    for validator in transform.validators:
        # Options-aware validators (e.g. depicted ⊆ cast) opt in via a `wants_options`
        # marker; the common case stays single-arg (DESIGN §6 Validator contract).
        if getattr(validator, "wants_options", False):
            reason = validator(output, options)
        else:
            reason = validator(output)
        if reason is None:
            continue
        if reason.startswith("warn:"):
            warnings.append(reason[len("warn:") :])
            continue
        return None, reason, []

    return output, None, warnings


async def run_transform(
    transform: Transform,
    text: str,
    options: dict,
    llm,
    gate: GenerationGate,
    lease: GpuLease | None = None,
    request_id: str | None = None,
) -> dict:
    """Run the full §3 pipeline for one request. Raises :class:`TransformError`.

    ``lease`` (T22, ADR-0009) is the optional cross-process GPU tenancy lease. When present,
    the generation region runs inside ``lease.region(...)`` and the flock is acquired inside
    the slot before generating, so a burst holds the GPU once and drains under one lease.
    """
    _validate_options(transform, options)
    text, truncated = _enforce_budget(transform, text)
    input_tokens_est = estimate_tokens(text)
    messages = render_messages(transform.template, text, options)

    reasons: list[str] = []
    output: dict | None = None
    warnings: list[str] = []
    attempts = 0
    raw = ""  # last attempt's raw output; surfaced in the 422 detail on total failure

    # Hold the GPU lease across the whole region (outside the slot) so a burst is not freed
    # between two queued requests (T22, ADR-0009). No-op when no lease is configured.
    region = lease.region(request_id) if lease is not None else nullcontext()
    # Acquire the single-in-flight generation slot, queueing up to queue_wait_s and
    # bounded by max_queue_depth (ADR-0005 / T14 ADR-0008). Timeout or a full queue ->
    # 503 busy. The gate releases the slot on exit.
    async with region, gate.slot() as queued_ms:
        gen_start = time.perf_counter()
        try:
            # Own the GPU before generating: acquire the shared flock inside the slot, so
            # the slot-winner is the sole acquirer and the burst amortizes one model load
            # (T22, ADR-0009). Fails open if the lockfile is unusable.
            if lease is not None:
                await lease.acquire_for_generation(transform.model, request_id)
            # Reload-on-demand inside the slot: a prior unload / idle expiry can't leave
            # this caller with model_unavailable (T14).
            await llm.ensure_loaded(transform.model)
            for attempt in range(transform.retries + 1):
                attempts = attempt + 1
                temperature = transform.temperature + transform.temp_bump * attempt
                params = {
                    "model": transform.model,
                    "temperature": temperature,
                    "top_p": transform.top_p,
                    "num_predict": transform.num_predict,
                    "num_ctx": transform.num_ctx,
                    "think": transform.think,
                }
                raw = await llm.chat(messages, transform.output_schema, params)
                output, reason, warnings = _attempt_reason(transform, raw, options)
                if output is not None:
                    break
                reasons.append(reason)
        except LLMBackendError as exc:
            # Infrastructure failure (Ollama down / errored) from either the reload or a
            # generation, not a validation failure: fail fast, do not retry.
            raise TransformError(
                503,
                "model_unavailable",
                "generation backend unavailable",
                exc.detail or None,
            ) from exc

    if output is None:
        # Surface a bounded snippet of the last raw output. Truncation past the model's
        # context window yields empty/garbage that parses as "invalid JSON: … char 1";
        # the snippet makes that (and any other post-generation failure) diagnosable.
        raw_snippet = raw[:_RAW_SNIPPET_CHARS]
        logger.debug(
            "validation_failed transform=%s attempts=%d reasons=%r raw_snippet=%r",
            transform.name,
            attempts,
            reasons,
            raw_snippet,
        )
        raise TransformError(
            422,
            "validation_failed",
            "generation failed validation after retries",
            {"reasons": reasons, "raw_snippet": raw_snippet},
        )

    latency_ms = int((time.perf_counter() - gen_start) * 1000)
    meta = {
        "transform": transform.name,
        "transform_version": transform.version,
        "model": transform.model,
        "input_tokens_est": input_tokens_est,
        "truncated": truncated,
        "attempts": attempts,
        "latency_ms": latency_ms,
        "queued_ms": queued_ms,
    }
    # Soft-validator findings from the successful attempt (DESIGN §7.5 depicted ⊆ cast).
    # Additive and omitted when empty, so the §4 meta shape is unchanged in the common case.
    if warnings:
        meta["warnings"] = warnings
    return {"output": output, "meta": meta}
