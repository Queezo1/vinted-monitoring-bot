"""Zählt Vinted-Requests der letzten Stunde – für /status und zur Selbstkontrolle der Last."""

from __future__ import annotations

import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass

WINDOW_S = 3600


@dataclass(frozen=True)
class StatsSummary:
  total: int
  by_kind: dict[str, int]  # "session", "catalog", "user", "item_page"
  by_outcome: dict[str, int]  # "200", "401", "403", "429", "network", ...


class RequestStats:
  def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
    self._clock = clock
    self._events: deque[tuple[float, str, str]] = deque()

  def record(self, kind: str, outcome: str) -> None:
    self._events.append((self._clock(), kind, outcome))
    self._trim()

  def summary(self) -> StatsSummary:
    self._trim()
    return StatsSummary(
      total=len(self._events),
      by_kind=dict(Counter(kind for _, kind, _ in self._events)),
      by_outcome=dict(Counter(outcome for _, _, outcome in self._events)),
    )

  def _trim(self) -> None:
    cutoff = self._clock() - WINDOW_S
    while self._events and self._events[0][0] < cutoff:
      self._events.popleft()
