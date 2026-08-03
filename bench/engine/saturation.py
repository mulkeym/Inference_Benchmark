import asyncio
import time

import psutil


class SaturationMonitor:
    def __init__(self, lag_threshold_ms: float = 100.0, cpu_threshold: float = 90.0,
                 consecutive: int = 5):
        self.lag_threshold_ms = lag_threshold_ms
        self.cpu_threshold = cpu_threshold
        self.consecutive = consecutive
        self.saturated = False
        self._hits = 0
        self._task: asyncio.Task | None = None
        self._proc = psutil.Process()

    def start(self) -> None:
        self._proc.cpu_percent(None)
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            started = time.perf_counter()
            await asyncio.sleep(1.0)
            lag_ms = (time.perf_counter() - started - 1.0) * 1000
            cpu = self._proc.cpu_percent(None)
            if lag_ms > self.lag_threshold_ms or cpu > self.cpu_threshold:
                self._hits += 1
                if self._hits >= self.consecutive:
                    self.saturated = True
            else:
                self._hits = 0
