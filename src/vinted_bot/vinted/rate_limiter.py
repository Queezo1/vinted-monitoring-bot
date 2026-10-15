"""Globaler Rate-Limiter: Mindestabstand zwischen ZWEI BELIEBIGEN Vinted-Requests.

Gilt über alle Filter, Domains und Request-Arten (Session, Katalog, Verkäufer) hinweg.
Damit bleibt die Last von deiner IP gedeckelt, egal wie viele Filter laufen.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class GlobalRateLimiter:
  def __init__(
    self,
    min_spacing_s: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
  ) -> None:
    # clock/sleep sind injizierbar, damit Tests ohne echtes Warten laufen
    self._min_spacing_s = min_spacing_s
    self._clock = clock
    self._sleep = sleep
    self._lock = asyncio.Lock()
    self._last_request_at: float | None = None

  async def acquire(self) -> None:
    """Wartet, bis der nächste Request erlaubt ist. Aufrufer werden der Reihe nach bedient."""
    # Das Lock sorgt dafür, dass immer nur eine Coroutine gleichzeitig "die Uhr prüft"
    async with self._lock:
      if self._last_request_at is not None:
        wait_s = self._last_request_at + self._min_spacing_s - self._clock()
        if wait_s > 0:
          await self._sleep(wait_s)
      self._last_request_at = self._clock()
