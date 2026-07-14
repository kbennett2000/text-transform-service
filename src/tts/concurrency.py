"""The single-in-flight generation gate (ADR-0005, extended in T14 / ADR-0008).

ADR-0005 serializes all generation behind one slot: a request holds the slot while it
generates, and concurrent requests queue. This module encapsulates that slot plus two
bounds so the queueing is observable and can't grow without limit:

- ``queue_wait_s`` — the *time* bound (existing ``QUEUE_WAIT_S``): a waiter that does not
  get the slot within this window fails ``503 busy``.
- ``max_queue_depth`` — the *depth* bound (T14, ``MAX_QUEUE_DEPTH``): when set (>0), a
  request that arrives with the queue already full fast-fails ``503 busy`` immediately
  rather than waiting out ``queue_wait_s``. ``0`` = unbounded (pre-T14 behavior).

The unload route acquires the same slot, so an eviction can never race an in-flight
generation (T14). ``TransformError`` is reused verbatim so the ``503 busy`` shape is
identical whether it comes from the depth bound or the time bound.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from tts.pipeline import TransformError


class GenerationGate:
    """Serializes generation behind a single slot with time + depth bounds.

    ``waiters`` counts requests currently blocked waiting for the slot (not the one
    holding it). The depth check and the increment happen without an intervening
    ``await``, so under asyncio's cooperative scheduling they are atomic — no lock needed.
    """

    def __init__(
        self,
        queue_wait_s: float,
        max_queue_depth: int = 0,
        semaphore: asyncio.Semaphore | None = None,
    ):
        self._sem = semaphore if semaphore is not None else asyncio.Semaphore(1)
        self.queue_wait_s = queue_wait_s
        self.max_queue_depth = max_queue_depth
        self._waiters = 0

    @property
    def waiters(self) -> int:
        """Requests currently queued (blocked waiting for the slot)."""
        return self._waiters

    @asynccontextmanager
    async def slot(self):
        """Acquire the generation slot, yielding ``queued_ms``. Raises ``503 busy``.

        Fast-fails ``busy`` when the depth bound is hit; otherwise queues up to
        ``queue_wait_s`` and fails ``busy`` on timeout. The slot is always released on
        exit (mirrors the pre-T14 ``finally`` release in the pipeline).
        """
        if self.max_queue_depth > 0 and self._waiters >= self.max_queue_depth:
            raise TransformError(
                503,
                "busy",
                "generation queue is full",
                {"queue_depth": self._waiters, "max_queue_depth": self.max_queue_depth},
            )

        self._waiters += 1
        try:
            queue_start = time.perf_counter()
            try:
                await asyncio.wait_for(self._sem.acquire(), timeout=self.queue_wait_s)
            except TimeoutError as exc:
                raise TransformError(
                    503, "busy", "generation queue timed out", {"queue_wait_s": self.queue_wait_s}
                ) from exc
            queued_ms = int((time.perf_counter() - queue_start) * 1000)
        finally:
            # Stop counting as a waiter the moment we stop waiting — whether we acquired
            # the slot or timed out/were cancelled.
            self._waiters -= 1

        try:
            yield queued_ms
        finally:
            self._sem.release()
