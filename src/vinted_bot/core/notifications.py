"""Statusmeldungen für den Admin-Kanal – unabhängig von Discord definiert.

Poller und Dispatcher melden Ereignisse über `post()`. Das blockiert nie: Die Meldung landet
in einer Warteschlange, die der AdminNotifier (discord_bot/admin.py) abarbeitet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Severity(Enum):
  INFO = "info"  # grün: Start, wieder ok
  WARNING = "warning"  # orange: 429, wiederholte Fehler
  ERROR = "error"  # rot: 403, Filter pausiert


@dataclass(frozen=True)
class StatusEvent:
  severity: Severity
  title: str
  text: str
  # Gleicher Schlüssel innerhalb der Abkühlzeit -> Meldung wird unterdrückt (Entprellung)
  key: str


class Notifier(Protocol):
  def post(self, event: StatusEvent) -> None: ...


class NullNotifier:
  """Für Tests und wenn kein Admin-Kanal verfügbar ist."""

  def __init__(self) -> None:
    self.events: list[StatusEvent] = []

  def post(self, event: StatusEvent) -> None:
    self.events.append(event)
