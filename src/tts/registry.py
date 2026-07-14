"""Transform registry (DESIGN §6).

A transform is a frozen bundle of everything the pipeline needs to turn input text
into schema-constrained JSON: prompt template, model binding, sampling params, budget
policy, options/output JSON Schemas, post-generation validators, and retry policy.

Transforms are Python modules (type-checked, testable, greppable) — not config files.
Each is built in ``tts/transforms/`` and registered via :func:`register`. Duplicate
registration is a startup error, not a silent overwrite.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

# A validator inspects the parsed output object and returns ``None`` when the output
# is acceptable, or a human-readable reason string when it is not. Validators never
# mutate the output (DESIGN §7.2). A reason prefixed ``"warn:"`` is a soft finding the
# pipeline records to ``meta.warnings`` instead of failing (T6, DESIGN §7.5). Most
# validators take only the output; an options-aware one (e.g. depicted ⊆ cast) is called
# as ``validator(output, options)`` when it sets a ``wants_options`` marker on itself.
Validator = Callable[..., str | None]


@dataclass(frozen=True)
class Transform:
    """A registered text -> JSON transform (DESIGN §6, field-for-field)."""

    name: str
    version: str
    template: str  # Jinja2 source (SYSTEM/USER markers, see pipeline.render_messages)
    model: str
    temperature: float = 0.3
    top_p: float = 0.8
    num_predict: int = 512
    think: bool = False
    input_budget: int = 3000
    over_budget: Literal["truncate", "reject"] = "truncate"
    truncation_strategy: str = "head"
    # Ollama context window (tokens) for both prompt and generation. Ollama's runtime
    # default is only 4096 (T12): a large prompt fills it and starves generation, which
    # truncates mid-output -> invalid JSON -> 422. Left ``None``, it is computed in
    # __post_init__ as input_budget + num_predict + 1024 headroom so every transform's
    # full budget (prompt) plus output ceiling always fits. Override per transform only
    # when a model's true context or VRAM forces a smaller ceiling.
    num_ctx: int | None = None
    options_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    validators: tuple[Validator, ...] = ()
    retries: int = 1
    temp_bump: float = 0.15

    # Headroom (tokens) added to input_budget + num_predict when num_ctx is computed.
    # Covers the system framing, template scaffolding, and tokenizer estimate slack
    # (input_budget is measured in *estimated* tokens, which can undercount).
    _NUM_CTX_HEADROOM = 1024

    def __post_init__(self) -> None:
        # frozen dataclass: fill the computed default via object.__setattr__ so a
        # per-transform override (a non-None num_ctx) still wins.
        if self.num_ctx is None:
            object.__setattr__(
                self,
                "num_ctx",
                self.input_budget + self.num_predict + self._NUM_CTX_HEADROOM,
            )


REGISTRY: dict[str, Transform] = {}


def register(t: Transform) -> Transform:
    """Add a transform to the registry.

    Raises ``ValueError`` on a duplicate name — a duplicate registration is a build
    mistake and must fail loudly at startup, not silently clobber an existing entry.
    Returns the transform so modules can ``ECHO = register(build_echo())`` if desired.
    """
    if t.name in REGISTRY:
        raise ValueError(f"transform already registered: {t.name!r}")
    REGISTRY[t.name] = t
    return t
