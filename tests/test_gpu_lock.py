"""Tests for the cross-process GPU tenancy lease (T22, ADR-0009), non-gpu.

These drive the real ``run_transform`` pipeline with a residency-aware stub LLM and a real
``flock`` on a ``tmp_path`` lockfile — no live model. They assert the four lease guarantees:
held-while-busy (one load per burst), free-before-release ordering, fail-open on an unusable
lockfile / when disabled, and the MAX_HOLD non-preemptive yield. Timing follows the repo's
existing async style (tiny real ``asyncio.sleep``, ``asyncio.gather``); no fake clocks.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os

from tts.concurrency import GenerationGate
from tts.gpu_lock import GpuLease
from tts.pipeline import run_transform
from tts.registry import Transform

ECHO_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["echo"],
    "properties": {"echo": {"type": "string"}},
}
_MODEL = "qwen3:0.6b"


def _transform(**overrides) -> Transform:
    base = dict(
        name="t",
        version="0.1.0",
        template="USER:\n{{ text }}",
        model=_MODEL,
        output_schema=ECHO_SCHEMA,
    )
    base.update(overrides)
    return Transform(**base)


class _LeaseLLM:
    """Residency-aware stub: ``ensure_loaded`` loads (counted), ``unload`` evicts (recorded)."""

    def __init__(self, response: str = '{"echo": "ok"}', on_unload=None, delay_s: float = 0.0):
        self._response = response
        self._delay_s = delay_s
        self._loaded: set[str] = set()
        self.load_count = 0
        self.unloaded: list[str] = []
        self._on_unload = on_unload  # sync hook(model) for ordering assertions

    async def chat(self, messages, format_schema, params) -> str:
        if self._delay_s:
            await asyncio.sleep(self._delay_s)  # hold the slot so a burst genuinely queues
        return self._response

    async def ensure_loaded(self, model: str) -> None:
        if model not in self._loaded:
            self._loaded.add(model)
            self.load_count += 1

    async def list_loaded(self) -> list[str]:
        return sorted(self._loaded)

    async def unload(self, model: str) -> None:
        self.unloaded.append(model)
        self._loaded.discard(model)
        if self._on_unload is not None:
            self._on_unload(model)


def _lease(path: str, llm, **overrides) -> GpuLease:
    params = dict(
        path=path,
        max_hold_s=60.0,
        idle_grace_s=0.02,
        acquire_timeout_s=1.0,
        enabled=True,
    )
    params.update(overrides)
    return GpuLease(llm, **params)


async def _run(llm, gate, lease, text: str = "hi") -> dict:
    return await run_transform(_transform(), text, {}, llm, gate, lease, "req")


async def test_held_while_busy_loads_once(tmp_path):
    # A concurrent burst serializes through the one slot under a single held lease: the model
    # loads exactly once and the flock is acquired exactly once — no per-request reload.
    llm = _LeaseLLM()
    gate = GenerationGate(queue_wait_s=5.0)
    lease = _lease(str(tmp_path / "lock"), llm, idle_grace_s=5.0)
    await asyncio.gather(*[_run(llm, gate, lease) for _ in range(10)])
    assert llm.load_count == 1
    assert llm.unloaded == []  # still held (idle grace not elapsed)
    assert lease._lease_seq == 1  # acquired once for the whole burst
    await lease.aclose()


async def test_free_before_release_ordering(tmp_path):
    # On free, the lease must still hold the flock (a fresh non-blocking acquire fails), then
    # release only after the unload — so the next tenant always finds clear VRAM.
    path = str(tmp_path / "lock")
    probe: dict[str, bool] = {}

    def on_unload(_model: str) -> None:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            probe["held_during_free"] = False  # acquired -> already released (WRONG order)
            fcntl.flock(fd, fcntl.LOCK_UN)
        except BlockingIOError:
            probe["held_during_free"] = True  # still held during free (correct)
        finally:
            os.close(fd)

    llm = _LeaseLLM(on_unload=on_unload)
    gate = GenerationGate(queue_wait_s=5.0)
    lease = _lease(path, llm, idle_grace_s=0.02)
    await _run(llm, gate, lease)
    await asyncio.sleep(0.15)  # let the idle-free timer fire

    assert llm.unloaded == [_MODEL]  # VRAM was freed
    assert probe.get("held_during_free") is True
    # And after release the lock is genuinely free.
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # must not raise
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    await lease.aclose()


async def test_fail_open_when_lockfile_unopenable(tmp_path, caplog):
    # An unopenable lockfile (missing parent dir) fails open: generation still succeeds, the
    # lease never holds, nothing is unloaded, and the fail-open is logged.
    llm = _LeaseLLM()
    gate = GenerationGate(queue_wait_s=5.0)
    lease = _lease(str(tmp_path / "no-such-dir" / "lock"), llm)
    with caplog.at_level(logging.WARNING, logger="tts.gpu_lock"):
        result = await _run(llm, gate, lease)
    assert result["output"] == {"echo": "ok"}
    assert lease._held is False
    assert llm.unloaded == []
    assert "fail-open" in caplog.text
    await lease.aclose()


async def test_disabled_proceeds_without_lock(tmp_path, caplog):
    # GPU_LOCK_ENABLED=false: passthrough — never opens the lockfile, never holds, still serves.
    llm = _LeaseLLM()
    gate = GenerationGate(queue_wait_s=5.0)
    lease = _lease(str(tmp_path / "lock"), llm, enabled=False)
    with caplog.at_level(logging.WARNING, logger="tts.gpu_lock"):
        result = await _run(llm, gate, lease)
    assert result["output"] == {"echo": "ok"}
    assert lease._held is False
    assert lease._fd_opened is False  # never even touched the file
    assert "disabled" in caplog.text


async def test_max_hold_yields_midburst(tmp_path):
    # max_hold_s=0: as long as work is queued behind the current request, the lease frees +
    # releases + re-acquires between generations (non-preemptive) — so the peer gets a turn.
    llm = _LeaseLLM(delay_s=0.02)  # each gen holds the slot so the rest queue behind it
    gate = GenerationGate(queue_wait_s=5.0)
    lease = _lease(str(tmp_path / "lock"), llm, max_hold_s=0.0, idle_grace_s=5.0)
    await asyncio.gather(*[_run(llm, gate, lease) for _ in range(4)])
    assert len(llm.unloaded) >= 1  # freed mid-burst at MAX_HOLD
    assert llm.load_count >= 2  # reloaded after yielding
    assert lease._lease_seq >= 2  # re-acquired at least once
    await lease.aclose()
