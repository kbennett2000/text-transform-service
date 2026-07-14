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
from tts.transforms.cast_canonicalize import build_cast_canonicalize
from tts.transforms.cast_mentions import build_cast_mentions
from tts.transforms.echo import build_echo
from tts.transforms.illustration_prompt import build_illustration_prompt
from tts.transforms.image_prompt import build_image_prompt
from tts.transforms.scene_update import build_scene_update
from tts.transforms.story_cover import build_story_cover

pytestmark = pytest.mark.gpu

TEST_MODEL = "qwen3.5:2b"

_NEWS_FIXTURES = Path(__file__).parent / "fixtures" / "news"
_BOOK_FIXTURES = Path(__file__).parent / "fixtures" / "book"
_STORY_COVER_FIXTURES = Path(__file__).parent / "fixtures" / "story_cover"


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


# --- T5: cast-mentions + cast-canonicalize on the real production model (qwen3.5:9b) -------

CAST_MODEL = "qwen3.5:9b"


async def test_cast_mentions_all_book_fixtures_schema_valid_and_printed(client, capsys):
    """Run all 4 Time Machine excerpts through the real cast-mentions transform on
    qwen3.5:9b. The pipeline enforces the §7.2 mentions schema and the nested
    no_empty_strings validator, so a returned result IS the schema+validator assertion.
    We assert shape/mechanics + a loose check on the zero-character page, and print the
    mentions for the human eyeball (descriptors must be verbatim-ish quotes, not inventions).
    """
    transform = build_cast_mentions()
    assert transform.model == CAST_MODEL

    # The T5 per-case excerpts are the numbered files (01–04); the T6 page_*.txt fixtures
    # (consecutive pages for scene-update) live alongside them and are excluded here.
    fixtures = sorted(_BOOK_FIXTURES.glob("0*.txt"))
    assert len(fixtures) == 4, f"expected 4 excerpts, found {[f.name for f in fixtures]}"

    lines: list[str] = []
    for i, path in enumerate(fixtures):
        text = path.read_text(encoding="utf-8")
        result = await run_transform(
            transform, text, {}, client, asyncio.Semaphore(1), 120.0
        )
        output, meta = result["output"], result["meta"]

        # Shape/mechanics only — schema + validators already passed inside the pipeline.
        assert set(output) == {"mentions"}
        assert isinstance(output["mentions"], list)
        assert meta["model"] == CAST_MODEL

        if path.name == "02_description.txt":
            # Zero-character page (pure time-travel description). DESIGN calls for a LOOSE
            # assertion here, logging the actual result: this excerpt is first-person
            # narration, so the model may reasonably surface the lone narrator ("I", i.e.
            # the Time Traveller) — what it must NOT do is invent a populated cast. We
            # assert it stays empty / non-person, or at most the single narrator.
            mentions = output["mentions"]
            assert (
                mentions == []
                or all(not m["is_person"] for m in mentions)
                or len(mentions) <= 1
            ), f"pure-description page invented a cast: {mentions}"

        tag = "cold" if i == 0 else "warm"
        names = [f"{m['name']} (person={m['is_person']})" for m in output["mentions"]]
        lines.append(
            f"[{path.name}] latency_ms={meta['latency_ms']} ({tag}) "
            f"attempts={meta['attempts']} mentions={len(output['mentions'])}\n"
            f"  names: {names}\n"
            f"  full: {json.dumps(output['mentions'], ensure_ascii=False)}"
        )

    with capsys.disabled():
        print("\n\n=== T5 cast-mentions GPU outputs (qwen3.5:9b) ===")
        for line in lines:
            print(line)
        print("=== end cast-mentions outputs ===\n")


async def test_cast_canonicalize_fixture_schema_valid_and_printed(client, capsys):
    """Run the hand-assembled 'the Time Traveller' evidence payload through the real
    cast-canonicalize transform on qwen3.5:9b. Assert the §7.3 output bounds plus a
    tolerant sentence-count check; print the canonical entry for the human eyeball
    (paintable + era-plausible; drawn from the evidence).
    """
    transform = build_cast_canonicalize()
    assert transform.model == CAST_MODEL

    options = json.loads(
        (_BOOK_FIXTURES / "canonicalize_time_traveller.json").read_text(encoding="utf-8")
    )
    result = await run_transform(
        transform, "", options, client, asyncio.Semaphore(1), 120.0
    )
    output, meta = result["output"], result["meta"]

    assert set(output) == {"visual_description", "one_line", "tags"}
    assert meta["model"] == CAST_MODEL
    assert len(output["one_line"]) <= 160
    # Tolerant sentence count: split on ". " and drop empties. Expect 2-4 sentences.
    prose = output["visual_description"].replace("\n", " ")
    sentences = [s for s in prose.split(". ") if s.strip()]
    assert 2 <= len(sentences) <= 4, (
        f"expected 2-4 sentences, got {len(sentences)}: {output['visual_description']!r}"
    )

    with capsys.disabled():
        print("\n\n=== T5 cast-canonicalize GPU output (qwen3.5:9b) ===")
        print(f"latency_ms={meta['latency_ms']} attempts={meta['attempts']}")
        print(f"one_line: {output['one_line']}")
        print(f"visual_description: {output['visual_description']}")
        print(f"tags: {output['tags']}")
        print("=== end cast-canonicalize output ===\n")


# --- T6: scene-update (sequential threading) + illustration-prompt (qwen3.5:9b) -----------

_LEDGER_FIELDS = {
    "location",
    "time_of_day",
    "atmosphere",
    "present",
    "scene_changed",
    "visual_salience",
    "best_visual_beat",
    "carry_notes",
}


async def test_scene_update_threading_then_illustration_prompt(client, capsys):
    """The T6 end-to-end GPU flow on qwen3.5:9b.

    Thread 3 *consecutive* Time Machine pages through scene-update, feeding each returned
    ledger into the next call's ``prior_ledger`` (the DESIGN §7.4 strictly-in-order pattern).
    Assert every ledger is schema-valid (the pipeline enforces the §7.4 schema + the
    best_visual_beat validator, so a returned result IS that assertion), with a non-empty
    location and visual_salience in [0,1]. Then run illustration-prompt on the highest-salience
    page using the T5 canonical Time Traveller entry: assert schema + hard validators pass (no
    raise) and that the soft ``depicted ⊆ cast`` check is recorded, not fatal (200; warnings
    may be present or empty). Everything is printed for the human eyeball CYCLE-LOG paste
    (location carries across the non-moving smoking-room pages; beats are concrete).
    """
    scene = build_scene_update()
    assert scene.model == CAST_MODEL

    start = json.loads((_BOOK_FIXTURES / "scene_start.json").read_text(encoding="utf-8"))
    pages = ["page_a.txt", "page_b.txt", "page_c.txt"]

    ledgers: list[dict] = []
    lines: list[str] = []
    prior = start["prior_ledger"]  # None on page 1
    for i, name in enumerate(pages):
        text = (_BOOK_FIXTURES / name).read_text(encoding="utf-8")
        options = {
            "prior_ledger": prior,
            "cast_names": start["cast_names"],
            "era": start["era"],
        }
        result = await run_transform(scene, text, options, client, asyncio.Semaphore(1), 120.0)
        output, meta = result["output"], result["meta"]

        # Shape/mechanics only — schema + validator already passed inside the pipeline.
        assert set(output) == _LEDGER_FIELDS
        assert meta["model"] == CAST_MODEL
        assert isinstance(output["location"], str) and output["location"].strip()
        assert 0.0 <= output["visual_salience"] <= 1.0

        ledgers.append(output)
        prior = output  # thread this ledger into the next page's prior_ledger
        tag = "cold" if i == 0 else "warm"
        lines.append(
            f"[{name}] latency_ms={meta['latency_ms']} ({tag}) attempts={meta['attempts']}\n"
            f"  {json.dumps(output, ensure_ascii=False)}"
        )

    # Highest-salience page -> illustration-prompt with the T5 canonical cast entry.
    best_idx = max(range(len(ledgers)), key=lambda i: ledgers[i]["visual_salience"])
    best_page = pages[best_idx]
    best_ledger = ledgers[best_idx]
    cast = json.loads((_BOOK_FIXTURES / "illustration_cast.json").read_text(encoding="utf-8"))

    illus = build_illustration_prompt()
    assert illus.model == CAST_MODEL
    ip_options = {"ledger": best_ledger, "cast": cast, "era": start["era"]}
    ip_result = await run_transform(
        illus,
        (_BOOK_FIXTURES / best_page).read_text(encoding="utf-8"),
        ip_options,
        client,
        asyncio.Semaphore(1),
        120.0,
    )
    ip_out, ip_meta = ip_result["output"], ip_result["meta"]

    # Schema + hard validators (word_range, banned_substrings) already passed inside the
    # pipeline; the soft depicted-subset check is non-fatal (this call returned 200).
    assert {"prompt", "depicted", "shot"} <= set(ip_out)
    assert ip_meta["model"] == CAST_MODEL

    with capsys.disabled():
        print("\n\n=== T6 scene-update sequential ledgers (qwen3.5:9b) ===")
        for line in lines:
            print(line)
        print(f"=== T6 illustration-prompt on max-salience page [{best_page}] ===")
        print(f"latency_ms={ip_meta['latency_ms']} attempts={ip_meta['attempts']} "
              f"warnings={ip_meta.get('warnings')}")
        print(f"prompt: {ip_out['prompt']}")
        print(f"depicted: {ip_out['depicted']}  shot: {ip_out['shot']}  "
              f"avoid: {ip_out.get('avoid')}")
        print("=== end T6 outputs ===\n")


# --- T9: story-cover on the real production model (qwen3.5:9b) -----------------------------

STORY_COVER_MODEL = "qwen3.5:9b"
_CATEGORIES = {
    "WORLD", "POLITICS", "BUSINESS", "TECHNOLOGY",
    "SCIENCE", "SPORTS", "CULTURE", "OPINION",
}


async def test_story_cover_all_fixtures_schema_valid_and_printed(client, capsys):
    """Run all 5 synthetic story-cover fixtures through the real transform on qwen3.5:9b.
    The pipeline enforces the reconciled five-field schema (incl. the category enum) *and* the
    subject-neutral validators (banned_substrings + word_range), so a returned result (no
    TransformError, no 422) IS the schema+validator assertion. We never assert wording; the
    bundles are printed for the human eyeball paste into CYCLE-LOG (the reconciliation to check
    is that imagePrompt/caption stay subject-only — no style/medium words, no baked-in
    toy-brick treatment). First fixture is a cold load; the rest are warm.
    """
    transform = build_story_cover()
    assert transform.model == STORY_COVER_MODEL

    fixtures = sorted(_STORY_COVER_FIXTURES.glob("*.txt"))
    assert len(fixtures) == 5, f"expected 5 fixtures, found {[f.name for f in fixtures]}"

    lines: list[str] = []
    for i, path in enumerate(fixtures):
        text = path.read_text(encoding="utf-8")
        result = await run_transform(
            transform, text, {}, client, asyncio.Semaphore(1), 120.0
        )
        output, meta = result["output"], result["meta"]

        # Shape/mechanics only — schema + validators already passed inside the pipeline.
        assert set(output) == {"headline", "description", "imagePrompt", "category", "caption"}
        assert all(isinstance(output[k], str) and output[k].strip() for k in output)
        assert output["category"] in _CATEGORIES
        assert meta["model"] == STORY_COVER_MODEL
        # Single-paragraph input -> head truncation is a structural no-op (see budget.py).
        assert meta["truncated"] is False

        tag = "cold" if i == 0 else "warm"
        lines.append(
            f"[{path.name}] category={output['category']} "
            f"latency_ms={meta['latency_ms']} ({tag}) attempts={meta['attempts']}\n"
            f"  headline: {output['headline']}\n"
            f"  description: {output['description']}\n"
            f"  imagePrompt: {output['imagePrompt']}\n"
            f"  caption: {output['caption']}"
        )

    with capsys.disabled():
        print("\n\n=== T9 story-cover GPU outputs (qwen3.5:9b) ===")
        for line in lines:
            print(line)
        print("=== end story-cover outputs ===\n")
