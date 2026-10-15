"""Nach dem Posten: Fotos, Beschreibung und Verkäuferinfos nachladen und die Nachricht ergänzen.

Warum nachträglich? Jede Zusatzanfrage kostet wegen des Mindestabstands ein paar Sekunden.
Früher wurde vor dem Posten nachgeladen – im Schnitt 12,7 s Verzögerung (gemessen am
15.10.2026). Jetzt steht der Artikel sofort in Discord, die Galerie erscheint kurz danach.

Alle Anfragen hier laufen mit NIEDRIGER Priorität: Sie warten, solange der Katalog-Abruf
(der neue Artikel findet) an der Reihe ist.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from vinted_bot.core.notifications import (
  Notifier,
  NullNotifier,
  Severity,
  StatusEvent,
  quote_error,
)
from vinted_bot.core.seller_lookup import SellerResolver
from vinted_bot.storage.repository import OutboxEntry, utc_now
from vinted_bot.vinted.client import VintedClient
from vinted_bot.vinted.errors import BlockedError, RateLimitedError, VintedError
from vinted_bot.vinted.item_page import parse_item_page

log = logging.getLogger(__name__)

# Älteres lohnt nicht mehr (und hält nur die Warteschlange auf)
MAX_JOB_AGE = timedelta(minutes=10)
# Nach 403/429 beim Nachladen so lange pausieren (der Katalog läuft weiter)
PAUSE_AFTER_BLOCK = timedelta(minutes=15)


@dataclass(frozen=True)
class EnrichJob:
  entry: OutboxEntry
  handle: Any  # z. B. die Discord-Nachricht; der Enricher kennt Discord nicht
  submitted_at: datetime


class Enricher:
  def __init__(
    self,
    client: VintedClient,
    sellers: SellerResolver,
    edit: Callable[[Any, OutboxEntry], Awaitable[None]],
    *,
    seller_lookup: bool,
    item_details: bool,
    notifier: Notifier | None = None,
    on_throttled: Callable[[], None] | None = None,
    now: Callable[[], datetime] = utc_now,
  ) -> None:
    self._client = client
    self._sellers = sellers
    self._edit = edit
    self._seller_lookup = seller_lookup
    self._item_details = item_details
    self._notifier = notifier or NullNotifier()
    self._on_throttled = on_throttled or (lambda: None)
    self._now = now
    self._queue: asyncio.Queue[EnrichJob] = asyncio.Queue()
    self._paused_until: datetime | None = None

  def submit(self, entry: OutboxEntry, handle: Any) -> None:
    if self._needs_work(entry):
      self._queue.put_nowait(EnrichJob(entry, handle, self._now()))

  def pending(self) -> int:
    return self._queue.qsize()

  async def run(self) -> None:
    while True:
      job = await self._queue.get()
      try:
        await self.process(job)
      except Exception:
        log.exception("Nachladen für Artikel %s fehlgeschlagen", job.entry.item_id)

  async def process(self, job: EnrichJob) -> bool:
    """Lädt nach und bearbeitet die Nachricht. True, wenn sie aktualisiert wurde."""
    now = self._now()
    if now - job.submitted_at > MAX_JOB_AGE:
      return False
    if self._paused_until is not None and now < self._paused_until:
      return False

    entry = job.entry
    item, seller = entry.item, entry.seller
    try:
      if seller is None and self._seller_lookup and item.seller_id is not None:
        seller = await self._sellers.resolve(entry.domain, item.seller_id, low_priority=True)
      if self._item_details:
        page = await self._client.fetch_item_page(entry.domain, item.id, low_priority=True)
        details = parse_item_page(page, item.id, item.title)
        item = replace(
          item,
          photo_urls=details.photo_urls or item.photo_urls,
          description=details.description or item.description,
        )
    except (BlockedError, RateLimitedError) as error:
      self._pause(entry.domain, error)
      # Was bis dahin geladen wurde, trotzdem anzeigen
    except VintedError as error:
      log.warning("Nachladen für Artikel %s: %s", item.id, error)

    updated = replace(entry, item=item, seller=seller)
    if updated == entry:
      return False
    await self._edit(job.handle, updated)
    return True

  def _needs_work(self, entry: OutboxEntry) -> bool:
    missing_seller = entry.seller is None and self._seller_lookup
    return self._item_details or missing_seller

  def _pause(self, domain: str, error: VintedError) -> None:
    self._paused_until = self._now() + PAUSE_AFTER_BLOCK
    self._on_throttled()
    log.warning("Nachladen pausiert bis %s: %s", self._paused_until.isoformat(), error)
    self._notifier.post(
      StatusEvent(
        Severity.WARNING,
        f"Nachladen auf vinted.{domain} abgewiesen",
        f"{quote_error(error)}\nNeue Artikel werden weiter sofort gepostet, aber 15 min lang "
        "ohne Galerie/Beschreibung (bzw. ohne Flagge/Bewertung bei Filtern ohne Länderfilter).",
        key=f"enrich:{domain}",
      )
    )
