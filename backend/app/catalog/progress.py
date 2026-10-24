"""События синхронизации для потока SSE: хранятся в памяти, поздний подписчик получает всю историю."""

import asyncio
from collections.abc import AsyncIterator


class SyncProgress:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.finished = False
        self._changed = asyncio.Condition()

    async def emit(self, **event) -> None:
        async with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    async def close(self) -> None:
        async with self._changed:
            self.finished = True
            self._changed.notify_all()

    async def stream(self, heartbeat: float = 15.0) -> AsyncIterator[dict | None]:
        """Отдаёт события по порядку; None — сигнал «живое соединение» (heartbeat)."""
        index = 0
        while True:
            async with self._changed:
                try:
                    await asyncio.wait_for(
                        self._changed.wait_for(lambda: index < len(self.events) or self.finished), heartbeat
                    )
                except TimeoutError:
                    pending = None
                else:
                    pending = self.events[index:]
                    index = len(self.events)
                finished = self.finished and index >= len(self.events)
            if pending is None:
                yield None
                continue
            for event in pending:
                yield event
            if finished:
                return


class Registry:
    """Хранит прогресс последних синхронизаций."""

    def __init__(self, keep: int = 5) -> None:
        self._items: dict[int, SyncProgress] = {}
        self._keep = keep

    def create(self, sync_id: int) -> SyncProgress:
        progress = SyncProgress()
        self._items[sync_id] = progress
        for old in sorted(self._items)[: -self._keep]:
            del self._items[old]
        return progress

    def get(self, sync_id: int) -> SyncProgress | None:
        return self._items.get(sync_id)


registry = Registry()
