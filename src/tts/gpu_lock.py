"""Cross-process GPU tenancy lease (T22, ADR-0009).

One RTX 5070 hosts two GPU tenants: this service (Ollama) and imagegen-service (ComfyUI).
They coordinate through a single advisory file lock (``flock(2)``, ``LOCK_EX``) on a
well-known lockfile both open. Whoever holds it owns the whole GPU: it loads its model,
**drains all its currently-queued work under one lease** (hold-and-drain, so a burst loads
the model once), **frees its own VRAM before releasing**, then hands off. ``flock`` gives
crash-safety (the kernel releases it on process death — no TTL/heartbeat) and rough-FIFO
fairness for free.

This module is this service's half. It frees only its *own* VRAM (Ollama unload); it never
touches ComfyUI. The lease is layered **above** the in-process ``Semaphore(1)`` generation
gate (ADR-0005): the flock is acquired *inside* the slot (the slot-winner is the sole
would-be acquirer, so there is no in-process acquire race), held *across* successive slots,
and freed by a separate idle timer.

Fail-open by contract: the lock is a throughput optimization, not a safety gate — an
unusable lockfile logs a warning and generation proceeds unlocked. See ADR-0009.

Coordination with imagegen-service — three things must match byte-for-byte: the lockfile
path, the free-before-release ordering, and the fail-open semantics. Timing knobs may differ.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import time
from contextlib import asynccontextmanager

logger = logging.getLogger("tts.gpu_lock")

# Default lockfile (tmpfs, cleared on reboot) and the fallback if /run is unavailable.
_DEFAULT_PATH = "/run/gpu-tenant.lock"
_FALLBACK_PATH = "/var/lock/gpu-tenant.lock"

# Non-blocking acquire poll backoff bounds (seconds). The loop never blocks the event loop.
_ACQUIRE_POLL_MIN_S = 0.05
_ACQUIRE_POLL_MAX_S = 1.0


class GpuLease:
    """A cross-process GPU lease over a shared ``flock``.

    Construct via :meth:`from_settings` in the app; tests construct directly. All state is
    guarded by a single ``asyncio.Lock`` (``_mu``); the potentially-slow work (the flock
    acquire loop and the Ollama unload) runs without holding it where that would stall other
    coroutines.
    """

    def __init__(
        self,
        llm,
        *,
        path: str = _DEFAULT_PATH,
        max_hold_s: float = 60.0,
        idle_grace_s: float = 5.0,
        acquire_timeout_s: float = 120.0,
        enabled: bool = True,
    ):
        self._llm = llm
        self._path = path
        self._max_hold_s = max_hold_s
        self._idle_grace_s = idle_grace_s
        self._acquire_timeout_s = acquire_timeout_s
        self._enabled = enabled

        self._mu = asyncio.Lock()
        self._active = 0  # requests in the generation region (slot holder + waiters)
        self._held = False  # do we currently hold the flock?
        self._fd: int | None = None
        self._fd_opened = False  # we attempt the open exactly once (and warn once on failure)
        self._lease_started = 0.0  # monotonic time this lease was acquired (for MAX_HOLD)
        self._gen_count = 0  # generations drained under the current lease (for logging)
        self._lease_seq = 0  # increments per acquire, correlates lifecycle log lines
        self._idle_task: asyncio.Task | None = None
        self._warned_disabled = False

    @classmethod
    def from_settings(cls, settings, llm) -> GpuLease:
        """Build a lease from :class:`~tts.config.Settings` (the app wiring)."""
        return cls(
            llm,
            path=settings.gpu_lock_path,
            max_hold_s=settings.gpu_lock_max_hold_s,
            idle_grace_s=settings.gpu_lock_idle_grace_s,
            acquire_timeout_s=settings.gpu_lock_acquire_timeout_s,
            enabled=settings.gpu_lock_enabled,
        )

    # -- region: counts busyness so the lease is held across a burst -----------------------

    @asynccontextmanager
    async def region(self, request_id: str | None = None):
        """Wrap the whole generation region (outside the semaphore slot).

        Counts the slot holder **plus** waiters as ``_active``, so the lease is never freed
        between two queued requests. Cancels any pending idle-free on entry; on the last
        request leaving, schedules the idle-free timer.
        """
        async with self._mu:
            self._active += 1
            self._cancel_idle_locked()
        try:
            yield
        finally:
            async with self._mu:
                self._active -= 1
                if self._held:
                    self._gen_count += 1
                    if self._active == 0:
                        self._schedule_idle_locked(request_id)

    # -- acquire: taken inside the slot, before generating ---------------------------------

    async def acquire_for_generation(self, model: str, request_id: str | None = None) -> None:
        """Ensure we hold the GPU lease before generating (called while holding the slot).

        Honors MAX_HOLD (non-preemptive yield to a waiting peer) and fails open on any lock
        error or acquire timeout. ``model`` is accepted for symmetry/logging; the free path
        unloads whatever is resident, since this service is Ollama's only tenant.
        """
        if not self._enabled:
            if not self._warned_disabled:
                self._warned_disabled = True
                logger.warning(
                    "gpu-lock disabled (GPU_LOCK_ENABLED=false); proceeding without lock"
                )
            return

        # MAX_HOLD fairness: if we have held past the cap AND work is still queued behind us,
        # free + release now, then re-acquire below (back of the flock queue). Non-preemptive:
        # the current generation already finished; we only gate *starting* the next one.
        async with self._mu:
            over_hold = self._held and (time.monotonic() - self._lease_started) >= self._max_hold_s
            waiters_behind = self._active > 1
        if over_hold and waiters_behind:
            await self._free_and_release("max-hold", request_id)

        async with self._mu:
            if self._held:
                return
            fd = self._ensure_fd_locked()
            if fd is None:
                return  # fail-open: lockfile unusable -> proceed without the lock

        # The flock acquire may wait for the peer; run it WITHOUT holding _mu. Only the
        # slot-winner ever reaches here (the semaphore serializes), so there is no fd race.
        acquired = await self._flock_acquire(fd, request_id)
        if not acquired:
            return  # fail-open for this operation (timeout/error already logged)

        async with self._mu:
            self._held = True
            self._lease_started = time.monotonic()
            self._gen_count = 0
            self._lease_seq += 1
            logger.info(
                "gpu-lock acquired seq=%d request_id=%s path=%s",
                self._lease_seq,
                request_id,
                self._path,
            )

    async def aclose(self) -> None:
        """Cancel the idle timer, free+release if held, and close the fd (app shutdown/tests)."""
        async with self._mu:
            self._cancel_idle_locked()
            await self._free_and_release_locked("shutdown", None)
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
                self._fd_opened = False

    async def release_if_held(self, reason: str = "manual") -> None:
        """Free + release the lease if held. Used after a manual ``/v1/models/unload``.

        The manual route already freed VRAM under the slot; running the free path again is
        idempotent (``list_loaded`` returns nothing to unload) and drops GPU ownership so the
        peer can proceed.
        """
        async with self._mu:
            await self._free_and_release_locked(reason, None)

    # -- free + release (free-before-release ordering is the contract) ---------------------

    async def _free_and_release(self, reason: str, request_id: str | None) -> None:
        async with self._mu:
            await self._free_and_release_locked(reason, request_id)

    async def _free_and_release_locked(self, reason: str, request_id: str | None) -> None:
        """Unload our VRAM, THEN drop the flock. Caller holds ``_mu``.

        Holding ``_mu`` across the unload keeps a new request from starting a generation
        between free and release, so the next tenant always acquires into clear VRAM.
        """
        if not self._held:
            return
        seq = self._lease_seq
        drained = self._gen_count
        unloaded = await self._unload_all()  # FREE first
        self._release_flock()  # THEN release
        self._held = False
        logger.info(
            "gpu-lock freed (unloaded %s) and released seq=%d drained=%d reason=%s request_id=%s",
            ",".join(unloaded) if unloaded else "none",
            seq,
            drained,
            reason,
            request_id,
        )

    async def _unload_all(self) -> list[str]:
        """Unload every resident model. A free error never wedges the lease (best-effort)."""
        try:
            loaded = await self._llm.list_loaded()
        except Exception as exc:  # noqa: BLE001 - a free failure must not wedge the GPU
            logger.warning("gpu-lock: list_loaded failed during free (%s)", exc)
            return []
        unloaded: list[str] = []
        for model in loaded:
            try:
                await self._llm.unload(model)
                unloaded.append(model)
            except Exception as exc:  # noqa: BLE001 - keep freeing the rest; then release
                logger.warning("gpu-lock: unload(%s) failed during free (%s)", model, exc)
        return unloaded

    # -- idle-free timer -------------------------------------------------------------------

    def _schedule_idle_locked(self, request_id: str | None) -> None:
        self._idle_task = asyncio.create_task(self._idle_then_free(request_id))

    def _cancel_idle_locked(self) -> None:
        task = self._idle_task
        if task is not None:
            self._idle_task = None
            task.cancel()

    async def _idle_then_free(self, request_id: str | None) -> None:
        try:
            await asyncio.sleep(self._idle_grace_s)
        except asyncio.CancelledError:
            return
        async with self._mu:
            if self._active != 0 or not self._held:
                return  # work raced in during the grace, or already freed
            self._idle_task = None
            await self._free_and_release_locked("idle", request_id)

    # -- flock primitives ------------------------------------------------------------------

    def _ensure_fd_locked(self) -> int | None:
        """Open the lockfile once (lazily). Fail-open: on error ``_fd`` stays None."""
        if self._fd is not None or self._fd_opened:
            return self._fd
        self._fd_opened = True
        fd = self._open_path(self._path)
        if fd is None and self._path == _DEFAULT_PATH:
            fd = self._open_path(_FALLBACK_PATH)
            if fd is not None:
                self._path = _FALLBACK_PATH
        self._fd = fd
        return fd

    @staticmethod
    def _open_path(path: str) -> int | None:
        try:
            return os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            logger.warning(
                "gpu-lock fail-open: cannot open lockfile %s (%s); proceeding without lock",
                path,
                exc,
            )
            return None

    async def _flock_acquire(self, fd: int, request_id: str | None) -> bool:
        """Take ``LOCK_EX`` via a non-blocking poll loop bounded by the acquire timeout."""
        deadline = time.monotonic() + self._acquire_timeout_s
        backoff = _ACQUIRE_POLL_MIN_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "gpu-lock fail-open: acquire-timeout after %.1fs request_id=%s "
                        "(peer holds the GPU); proceeding without lock",
                        self._acquire_timeout_s,
                        request_id,
                    )
                    return False
                await asyncio.sleep(min(backoff, remaining))
                backoff = min(backoff * 2, _ACQUIRE_POLL_MAX_S)
            except OSError as exc:
                logger.warning(
                    "gpu-lock fail-open: flock error (%s) request_id=%s; proceeding without lock",
                    exc,
                    request_id,
                )
                return False

    def _release_flock(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError as exc:
                logger.warning("gpu-lock: LOCK_UN failed (%s)", exc)
