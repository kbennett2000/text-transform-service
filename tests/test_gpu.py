"""GPU integration tests (DESIGN §10) — run only on the 5070 with Ollama up.

    make test-gpu        # uv run pytest -m gpu

These hit real Ollama and assert **schema conformance and pipeline mechanics only** — never
model wording (model quality is irrelevant here; these only prove the plumbing). The fast
plumbing model is `qwen3.5:2b` (rebound from the absent `qwen3:0.6b`); production-transform
GPU tests (T4+) run on the real binding `qwen3.5:9b`. See docs/models.md.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tts.config import Settings
from tts.llm import OllamaClient
from tts.pipeline import run_transform
from tts.transforms.echo import build_echo
from tts.transforms.image_prompt import build_image_prompt

pytestmark = pytest.mark.gpu

TEST_MODEL = "qwen3.5:2b"

_NEWS_FIXTURES = Path(__file__).parent / "fixtures" / "news"


@pytest.fixture
def client() -> OllamaClient:
    s = Settings.from_env()
    return OllamaClient(base_url=s.ollama_url, keep_alive=s.ollama_keep_alive)


async def test_echo_transform_returns_schema_valid_output(client):
    # Full pipeline against a real model: echo is bound to qwen3.5:2b.
    t = build_echo()
    assert t.model == TEST_MODEL
    result = await run_transform(
        t, "Hello world. Second sentence.", {}, client, asyncio.Semaphore(1), 90.0
    )
    assert set(result["output"]) == {"echo"}
    assert isinstance(result["output"]["echo"], str)
    assert result["output"]["echo"]  # non-empty
    assert result["meta"]["model"] == TEST_MODEL
    assert result["meta"]["attempts"] >= 1
    assert result["meta"]["latency_ms"] >= 0


async def test_constrained_decoding_forces_schema_even_without_json_prompt(client):
    # The ADR-0002 guarantee: `format` grammar forces schema-valid JSON even when the prompt
    # never mentions JSON. This is why the client uses /api/generate (see docs/models.md).
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["sentence"],
        "properties": {"sentence": {"type": "string"}},
    }
    params = {
        "model": TEST_MODEL,
        "temperature": 0.0,
        "top_p": 0.8,
        "num_predict": 80,
        "think": False,
    }
    raw = await client.chat(
        [{"role": "user", "content": "Write one short sentence about the sea."}], schema, params
    )
    obj = json.loads(raw)  # must parse despite a non-JSON prompt
    assert set(obj) == {"sentence"}
    assert isinstance(obj["sentence"], str)


async def test_unload_empties_ps(client):
    # Load the model with a tiny generation, then unload and confirm /api/ps no longer lists it.
    params = {
        "model": TEST_MODEL,
        "temperature": 0.0,
        "top_p": 0.8,
        "num_predict": 8,
        "think": False,
    }
    await client.chat([{"role": "user", "content": "hi"}], {}, params)
    loaded_before = await client.list_loaded()
    assert any(TEST_MODEL in m for m in loaded_before), (
        f"expected {TEST_MODEL} loaded, got {loaded_before}"
    )

    await client.unload(TEST_MODEL)
    await asyncio.sleep(0.5)
    loaded_after = await client.list_loaded()
    assert all(TEST_MODEL not in m for m in loaded_after), f"still loaded: {loaded_after}"


# --- T4: image-prompt on the real production model (qwen3.5:9b) ---------------------------

IMAGE_PROMPT_MODEL = "qwen3.5:9b"


async def test_image_prompt_all_fixtures_schema_valid_and_printed(client, capsys):
    """Run all 5 synthetic news fixtures through the real image-prompt transform on
    qwen3.5:9b. The pipeline enforces the §7.1 output_schema *and* the transform's own
    validators (banned_substrings + word_range) — so a returned result (no TransformError,
    no 422) IS the schema+validator assertion. We never assert wording; the prompts are
    printed for the human eyeball paste into CYCLE-LOG. First fixture is a cold load; the
    rest are warm (latencies noted separately).
    """
    transform = build_image_prompt()
    assert transform.model == IMAGE_PROMPT_MODEL

    fixtures = sorted(_NEWS_FIXTURES.glob("*.txt"))
    assert len(fixtures) == 5, f"expected 5 fixtures, found {[f.name for f in fixtures]}"

    lines: list[str] = []
    for i, path in enumerate(fixtures):
        text = path.read_text(encoding="utf-8")
        result = await run_transform(
            transform, text, {}, client, asyncio.Semaphore(1), 120.0
        )
        output, meta = result["output"], result["meta"]

        # Shape/mechanics only — schema + validators already passed inside the pipeline.
        assert set(output) == {"prompt"}
        assert isinstance(output["prompt"], str) and output["prompt"].strip()
        assert meta["model"] == IMAGE_PROMPT_MODEL

        if path.name == "05_flood_long.txt":
            assert meta["truncated"] is True  # >3000 est-tokens -> lede_first_n truncation

        tag = "cold" if i == 0 else "warm"
        lines.append(
            f"[{path.name}] truncated={meta['truncated']} "
            f"latency_ms={meta['latency_ms']} ({tag}) attempts={meta['attempts']}\n"
            f"  -> {output['prompt']}"
        )

    with capsys.disabled():
        print("\n\n=== T4 image-prompt GPU outputs (qwen3.5:9b) ===")
        for line in lines:
            print(line)
        print("=== end image-prompt outputs ===\n")
