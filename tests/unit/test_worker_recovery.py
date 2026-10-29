from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from mishkan.workers.recovery import WorkerLeaseRecoveryDriver


@pytest.mark.anyio
async def test_recovery_driver_sweeps_immediately_and_stops_cleanly() -> None:
    observed = asyncio.Event()
    expired = (uuid4(),)
    calls = 0

    def sweep() -> tuple:
        nonlocal calls
        calls += 1
        observed.set()
        return expired

    driver = WorkerLeaseRecoveryDriver(60, sweep)
    driver.start()
    await asyncio.wait_for(observed.wait(), timeout=1)

    assert await driver.sweep() == expired
    await driver.stop()
    assert calls == 2


@pytest.mark.anyio
async def test_recovery_driver_survives_one_failed_sweep(caplog) -> None:  # type: ignore[no-untyped-def]
    recovered = asyncio.Event()
    calls = 0

    def sweep() -> tuple:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database temporarily unavailable")
        recovered.set()
        return ()

    driver = WorkerLeaseRecoveryDriver(0.01, sweep)
    driver.start()
    await asyncio.wait_for(recovered.wait(), timeout=1)
    await driver.stop()

    assert calls >= 2
    assert "worker lease recovery sweep failed" in caplog.text


def test_recovery_driver_rejects_invalid_interval() -> None:
    with pytest.raises(ValueError, match="interval"):
        WorkerLeaseRecoveryDriver(0, lambda: ())
