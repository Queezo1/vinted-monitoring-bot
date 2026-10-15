"""Startet und stoppt die Poller-Tasks (eine pro aktivem Filter)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from vinted_bot.core.poller import FilterPoller

log = logging.getLogger(__name__)

# Beim Start nicht alle Filter gleichzeitig abfragen, sondern gestaffelt
STARTUP_STAGGER_S = 5.0


class FilterManager:
  def __init__(self, make_poller: Callable[[int], FilterPoller]) -> None:
    self._make_poller = make_poller
    self._tasks: dict[int, asyncio.Task[None]] = {}

  def start(self, filter_id: int, *, delay_s: float = 0.0) -> None:
    if filter_id in self._tasks and not self._tasks[filter_id].done():
      return
    poller = self._make_poller(filter_id)
    self._tasks[filter_id] = asyncio.create_task(
      self._run(poller, delay_s), name=f"poller-{filter_id}"
    )

  def active_count(self) -> int:
    return sum(1 for task in self._tasks.values() if not task.done())

  def start_all(self, filter_ids: list[int]) -> None:
    for index, filter_id in enumerate(filter_ids):
      self.start(filter_id, delay_s=index * STARTUP_STAGGER_S)

  async def stop(self, filter_id: int) -> None:
    task = self._tasks.pop(filter_id, None)
    if task is not None:
      task.cancel()
      await asyncio.gather(task, return_exceptions=True)

  async def stop_all(self) -> None:
    for filter_id in list(self._tasks):
      await self.stop(filter_id)

  @staticmethod
  async def _run(poller: FilterPoller, delay_s: float) -> None:
    if delay_s:
      await asyncio.sleep(delay_s)
    try:
      await poller.run()
    except asyncio.CancelledError:
      raise
    except Exception:
      # Ein Programmierfehler in einem Filter darf die anderen nicht mitreißen
      log.exception("Poller für Filter %s abgestürzt", poller.filter_id)
