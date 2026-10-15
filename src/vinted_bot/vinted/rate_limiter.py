"""Globaler Rate-Limiter: Mindestabstand zwischen ZWEI BELIEBIGEN Vinted-Requests.

Gilt über alle Filter, Domains und Request-Arten (Session, Katalog, Verkäufer) hinweg.
Damit bleibt die Last von deiner IP gedeckelt, egal wie viele Filter laufen.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

MAX_SPACING_S = 10.0
RELAX_AFTER_S = 30 * 60


class GlobalRateLimiter:
  """Mindestabstand zwischen Requests, mit zwei Prioritäten.

  Niedrige Priorität (Fotos/Beschreibung nachladen) wartet, solange eine hohe Anfrage
  (Katalog, Länder-Lookup) ansteht – so verzögert das Nachladen nie das Finden neuer Artikel.
  """

  def __init__(
    self,
    min_spacing_s: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
  ) -> None:
    # clock/sleep sind injizierbar, damit Tests ohne echtes Warten laufen
    self._base_spacing_s = min_spacing_s
    self._min_spacing_s = min_spacing_s
    self._last_penalty_at: float | None = None
    self._clock = clock
    self._sleep = sleep
    self._lock = asyncio.Lock()
    self._last_request_at: float | None = None
    self._high_waiting = 0
    self._no_high_waiting = asyncio.Event()
    self._no_high_waiting.set()

  @property
  def spacing_s(self) -> float:
    """Aktueller Mindestabstand (steigt nach 429/403, sinkt in ruhigen Phasen wieder)."""
    return self._min_spacing_s

  def penalize(self) -> None:
    """Nach 429/403 aufrufen: Abstand × 1,5 (max. 10 s) – lieber langsamer als gesperrt."""
    self._min_spacing_s = min(MAX_SPACING_S, self._min_spacing_s * 1.5)
    self._last_penalty_at = self._clock()

  def _relax(self) -> None:
    # Alle 30 ruhigen Minuten eine Stufe schneller, bis zum Ausgangswert
    if self._last_penalty_at is None or self._min_spacing_s <= self._base_spacing_s:
      return
    if self._clock() - self._last_penalty_at >= RELAX_AFTER_S:
      self._min_spacing_s = max(self._base_spacing_s, self._min_spacing_s / 1.5)
      self._last_penalty_at = self._clock()

  async def acquire(self, *, low_priority: bool = False) -> None:
    """Wartet, bis der nächste Request erlaubt ist. Aufrufer werden der Reihe nach bedient."""
    if low_priority:
      # Hinten anstellen, solange wichtigere Anfragen warten
      await self._no_high_waiting.wait()
    else:
      self._high_waiting += 1
      self._no_high_waiting.clear()
    try:
      # Das Lock sorgt dafür, dass immer nur eine Coroutine gleichzeitig "die Uhr prüft"
      async with self._lock:
        self._relax()
        if self._last_request_at is not None:
          wait_s = self._last_request_at + self._min_spacing_s - self._clock()
          if wait_s > 0:
            await self._sleep(wait_s)
        self._last_request_at = self._clock()
    finally:
      if not low_priority:
        self._high_waiting -= 1
        if self._high_waiting == 0:
          self._no_high_waiting.set()
