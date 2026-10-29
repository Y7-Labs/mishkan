"""Daemon-owned lease expiry and loss-recovery wake-up loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from uuid import UUID

logger = logging.getLogger(__name__)


class WorkerLeaseRecoveryDriver:
    """Periodically expire abandoned leases; durable state remains repository-owned."""

    def __init__(
        self,
        interval_seconds: float,
        expire_leases: Callable[[], tuple[UUID, ...]],
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("worker lease recovery interval must be positive")
        self._interval_seconds = interval_seconds
        self._expire_leases = expire_leases
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="mishkan-worker-lease-recovery")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def sweep(self) -> tuple[UUID, ...]:
        return await asyncio.to_thread(self._expire_leases)

    async def _run(self) -> None:
        while True:
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("worker lease recovery sweep failed")
            await asyncio.sleep(self._interval_seconds)
