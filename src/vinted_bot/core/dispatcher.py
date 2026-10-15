"""Arbeitet die Outbox ab: ein Eintrag nach dem anderen, älteste zuerst.

Ein Artikel gilt erst als "gepostet", wenn Discord die Nachricht angenommen hat. Stürzt der
Bot zwischen Speichern und Senden ab, liegt der Eintrag beim Neustart noch in der Outbox.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from vinted_bot.storage.repository import OutboxEntry, Repository

log = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 5


class PermanentSendError(Exception):
  """Senden wird nie klappen (Kanal gelöscht, keine Rechte) -> nicht erneut versuchen."""


class Dispatcher:
  def __init__(
    self,
    repo: Repository,
    send: Callable[[OutboxEntry], Awaitable[Any]],
    *,
    idle_s: float = 5.0,
    max_attempts: int = MAX_SEND_ATTEMPTS,
    on_permanent_failure: Callable[[OutboxEntry, str], Awaitable[None]] | None = None,
    on_sent: Callable[[OutboxEntry, Any], None] | None = None,
  ) -> None:
    self._repo = repo
    self._send = send
    self._on_permanent_failure = on_permanent_failure
    # Erhält den Rückgabewert von send (die Discord-Nachricht) – für das spätere Nachladen
    self._on_sent = on_sent
    self._idle_s = idle_s
    self._max_attempts = max_attempts
    self._wake = asyncio.Event()

  def wake(self) -> None:
    """Vom Poller aufgerufen, wenn neue Einträge in der Outbox liegen."""
    self._wake.set()

  async def run(self) -> None:
    while True:
      sent = await self.flush_once()
      if sent == 0:
        # Schlafen, bis ein Poller weckt – spätestens nach idle_s erneut nachsehen
        self._wake.clear()
        try:
          await asyncio.wait_for(self._wake.wait(), timeout=self._idle_s)
        except TimeoutError:
          pass

  async def flush_once(self) -> int:
    """Versucht alle aktuell ausstehenden Einträge zu senden. Gibt die Anzahl Erfolge zurück."""
    sent = 0
    for entry in await self._repo.pending_outbox():
      try:
        handle = await self._send(entry)
      except PermanentSendError as error:
        log.error("Artikel %s (Filter %s) verworfen: %s", entry.item_id, entry.filter_id, error)
        await self._repo.mark_send_failed(entry.filter_id, entry.item_id, max_attempts=1)
        if self._on_permanent_failure is not None:
          await self._on_permanent_failure(entry, str(error))
        continue
      except Exception:
        log.exception("Senden von Artikel %s fehlgeschlagen", entry.item_id)
        gave_up = await self._repo.mark_send_failed(
          entry.filter_id, entry.item_id, max_attempts=self._max_attempts
        )
        if gave_up:
          log.error("Artikel %s nach %s Versuchen aufgegeben", entry.item_id, self._max_attempts)
        # Bei einem Fehler nicht sofort den nächsten Eintrag feuern (Discord schonen)
        break
      await self._repo.mark_posted(entry.filter_id, entry.item_id)
      sent += 1
      if self._on_sent is not None:
        self._on_sent(entry, handle)
    return sent
