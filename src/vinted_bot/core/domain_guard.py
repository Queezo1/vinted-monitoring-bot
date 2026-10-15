"""Sperre pro Vinted-Domain ("Circuit-Breaker").

403 und 429 betreffen nicht einen Filter, sondern deine IP bzw. Session. Würde jeder Filter
für sich weiterprobieren, klopfen bei 5 Filtern 5 Poller an eine verschlossene Tür. Deshalb:
Sobald EIN Poller 403/429 bekommt, pausieren ALLE Filter derselben Domain, bis die Sperrzeit
abgelaufen ist. Wiederholt sich der Fehler, wird die Pause länger (Eskalation).

  403: 15 min -> 30 min -> 1 h -> 2 h -> 4 h -> 6 h (Maximum)
  429: max(Retry-After, 2 min) -> verdoppelt bis max. 30 min
Ein erfolgreicher Abruf setzt die Stufe zurück.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from vinted_bot.storage.repository import utc_now
from vinted_bot.vinted.errors import BlockedError, RateLimitedError

BLOCKED_START = timedelta(minutes=15)
BLOCKED_MAX = timedelta(hours=6)
RATE_LIMITED_START = timedelta(minutes=2)
RATE_LIMITED_MAX = timedelta(minutes=30)


@dataclass(frozen=True)
class DomainBlock:
  domain: str
  reason: str  # "blocked" (403) oder "rate_limited" (429)
  until: datetime
  level: int  # 1 = erste Sperre, 2 = zweite in Folge, ...
  since: datetime  # Beginn der aktuellen Störung (für "seit"-Angaben)


@dataclass(frozen=True)
class Recovery:
  domain: str
  since: datetime  # wie lange es gestört war


class DomainGuard:
  def __init__(self, now: Callable[[], datetime] = utc_now) -> None:
    self._now = now
    self._blocks: dict[str, DomainBlock] = {}
    # Stufe merken, auch wenn die Sperre schon abgelaufen ist – bis zum nächsten Erfolg
    self._levels: dict[str, int] = {}
    self._since: dict[str, datetime] = {}

  def blocked_until(self, domain: str) -> datetime | None:
    block = self._blocks.get(domain)
    if block is None or block.until <= self._now():
      return None
    return block.until

  def current(self, domain: str) -> DomainBlock | None:
    """Aktive Sperre (für /status)."""
    return self._blocks.get(domain) if self.blocked_until(domain) else None

  def known_domains(self) -> list[str]:
    return sorted(set(self._blocks) | set(self._levels))

  def report_error(self, domain: str, error: BlockedError | RateLimitedError) -> DomainBlock:
    """Sperre setzen bzw. verlängern. Gibt die neue Sperre zurück."""
    now = self._now()
    existing = self.blocked_until(domain)
    if existing is not None:
      # Mehrere Poller liefen gleichzeitig in denselben Fehler -> nicht doppelt eskalieren
      return self._blocks[domain]

    level = self._levels.get(domain, 0) + 1
    self._levels[domain] = level
    since = self._since.setdefault(domain, now)
    if isinstance(error, RateLimitedError):
      start = RATE_LIMITED_START
      if error.retry_after_s is not None:
        start = max(start, timedelta(seconds=error.retry_after_s))
      duration = min(RATE_LIMITED_MAX, start * 2 ** (level - 1))
      reason = "rate_limited"
    else:
      duration = min(BLOCKED_MAX, BLOCKED_START * 2 ** (level - 1))
      reason = "blocked"
    block = DomainBlock(
      domain=domain, reason=reason, until=now + duration, level=level, since=since
    )
    self._blocks[domain] = block
    return block

  def report_success(self, domain: str) -> Recovery | None:
    """Erfolg setzt alles zurück. Gibt eine Recovery zurück, wenn vorher gestört war."""
    self._blocks.pop(domain, None)
    had_trouble = self._levels.pop(domain, 0) > 0
    since = self._since.pop(domain, None)
    if had_trouble and since is not None:
      return Recovery(domain=domain, since=since)
    return None
