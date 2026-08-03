import asyncio
from collections import defaultdict


class LiveHub:
    def __init__(self):
        self._subs: dict[int, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, test_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subs[test_id].append(queue)
        return queue

    def unsubscribe(self, test_id: int, queue: asyncio.Queue) -> None:
        if queue in self._subs.get(test_id, []):
            self._subs[test_id].remove(queue)

    def publish(self, test_id: int, kind: str, data: dict) -> None:
        for queue in list(self._subs.get(test_id, [])):
            queue.put_nowait({"type": kind, "data": data})
