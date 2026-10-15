"""Die Abfrageschleife für EINEN Filter. Jeder Filter läuft als eigene asyncio-Task.

Ein Durchlauf (`poll_once`):
  Katalog holen -> parsen -> Dedupe -> (Verkäufer nachschlagen -> Länderfilter)
  -> Ergebnis in EINER Transaktion speichern -> Dispatcher wecken.
`poll_once` gibt zurück, wie lange bis zum nächsten Durchlauf gewartet werden soll.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import replace

from vinted_bot.core.dedupe import select_new
from vinted_bot.core.seller_lookup import SellerResolver
from vinted_bot.storage.repository import FilterRow, NewItem, Repository
from vinted_bot.vinted.client import VintedClient
from vinted_bot.vinted.errors import (
  BlockedError,
  MalformedResponseError,
  RateLimitedError,
  UnexpectedStatusError,
  VintedError,
  VintedNetworkError,
)
from vinted_bot.vinted.item_page import parse_item_page
from vinted_bot.vinted.models import SellerInfo, VintedItem, parse_item

log = logging.getLogger(__name__)

JITTER = 0.15  # ±15 % Streuung, damit nicht alle Filter im Gleichschritt abfragen

# Backoff-Stufen je Fehlerart: (Start, Maximum) in Sekunden. Verdoppelt sich je Fehler in Folge.
BACKOFF_BLOCKED = (15 * 60, 6 * 3600)
BACKOFF_RATE_LIMITED = (2 * 60, 30 * 60)
BACKOFF_NETWORK = (10, 5 * 60)
BACKOFF_MALFORMED = (10 * 60, 60 * 60)


class FilterPoller:
  def __init__(
    self,
    filter_id: int,
    *,
    repo: Repository,
    client: VintedClient,
    sellers: SellerResolver,
    on_new_items: Callable[[], None],
    seller_lookup: bool = True,
    item_details: bool = True,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[], float] = random.random,
  ) -> None:
    self.filter_id = filter_id
    self._repo = repo
    self._client = client
    self._sellers = sellers
    self._on_new_items = on_new_items
    self._seller_lookup = seller_lookup
    self._item_details = item_details
    self._sleep = sleep
    self._rng = rng
    self._consecutive_errors = 0

  async def run(self) -> None:
    """Endlosschleife, bis der Filter gelöscht/pausiert oder die Task abgebrochen wird."""
    while True:
      flt = await self._repo.get_filter(self.filter_id)
      if flt is None or flt.paused:
        log.info("Filter %s gelöscht oder pausiert – Poller endet", self.filter_id)
        return
      delay_s = await self.poll_once(flt)
      await self._sleep(delay_s)

  async def poll_once(self, flt: FilterRow) -> float:
    try:
      await self._poll(flt)
    except VintedError as error:
      self._consecutive_errors += 1
      fail_count = await self._repo.record_poll_error(flt.id, f"{type(error).__name__}: {error}")
      delay_s = self._error_delay(error)
      log.warning(
        "Filter %s (%s): %s – %s. Fehler in Folge: %s, nächster Versuch in %.0f s",
        flt.id,
        flt.name,
        type(error).__name__,
        error,
        fail_count,
        delay_s,
      )
      return delay_s
    self._consecutive_errors = 0
    return self._jittered(flt.interval_s)

  async def _poll(self, flt: FilterRow) -> None:
    raw_items = await self._client.fetch_catalog(flt.domain, flt.api_params)
    items = [item for raw in raw_items if (item := parse_item(raw, flt.domain)) is not None]
    if len(items) < len(raw_items):
      log.warning("Filter %s: %s Artikel nicht lesbar", flt.id, len(raw_items) - len(items))

    seen = await self._repo.seen_item_ids(flt.id)
    result = select_new(items, seen, baseline_done=flt.baseline_done, max_seen_id=flt.max_seen_id)

    new_items: list[NewItem] = []
    skipped = list(result.skipped_ids)
    lookups_allowed = self._seller_lookup or bool(flt.countries)
    details_allowed = self._item_details
    for item in result.new_items:
      seller: SellerInfo | None = None
      if lookups_allowed and item.seller_id is not None:
        try:
          seller = await self._sellers.resolve(flt.domain, item.seller_id)
        except (BlockedError, RateLimitedError) as error:
          # Nicht weiter nachschlagen in diesem Durchlauf – die restlichen Artikel gehen
          # ohne Land/Bewertung raus, statt Vinted weiter zu bedrängen.
          log.warning("Verkäufer-Lookup gestoppt: %s", error)
          lookups_allowed = False
        except VintedError as error:
          log.warning("Verkäufer %s nicht abrufbar: %s", item.seller_id, error)

      if flt.countries and seller is not None and seller.country_iso is not None:
        if seller.country_iso not in flt.countries:
          skipped.append(item.id)
          continue
      elif flt.countries:
        # Land unbekannt: lieber posten (mit ❔) als einen Treffer verlieren
        log.info(
          "Filter %s: Land für Artikel %s unbekannt – wird trotzdem gepostet", flt.id, item.id
        )

      # Erst NACH dem Länderfilter: die Artikelseite nur für Artikel laden, die gepostet werden
      if details_allowed:
        try:
          item = await self._with_details(flt.domain, item)
        except (BlockedError, RateLimitedError) as error:
          log.warning("Artikelseiten-Abruf gestoppt: %s", error)
          details_allowed = False
        except VintedError as error:
          log.warning("Artikelseite %s nicht abrufbar: %s", item.id, error)
      new_items.append(NewItem(item=item, seller=seller))

    await self._repo.save_poll_result(
      flt.id,
      baseline_ids=result.baseline_ids,
      new_items=new_items,
      skipped_ids=skipped,
      max_seen_id=result.max_seen_id,
    )

    if result.baseline_ids:
      log.info(
        "Filter %s (%s): Baseline mit %s Artikeln gesetzt",
        flt.id,
        flt.name,
        len(result.baseline_ids),
      )
    if new_items or skipped:
      log.info(
        "Filter %s (%s): %s neu, %s übersprungen", flt.id, flt.name, len(new_items), len(skipped)
      )
    if new_items:
      self._on_new_items()

  async def _with_details(self, domain: str, item: VintedItem) -> VintedItem:
    page = await self._client.fetch_item_page(domain, item.id)
    details = parse_item_page(page, item.id, item.title)
    if not details.photo_urls:
      log.info("Artikel %s: keine Fotos auf der Artikelseite gefunden", item.id)
    return replace(
      item,
      photo_urls=details.photo_urls or item.photo_urls,
      description=details.description,
    )

  def _jittered(self, interval_s: float) -> float:
    return interval_s * (1 + JITTER * (2 * self._rng() - 1))

  def _error_delay(self, error: VintedError) -> float:
    if isinstance(error, BlockedError):
      start, cap = BACKOFF_BLOCKED
    elif isinstance(error, RateLimitedError):
      start, cap = BACKOFF_RATE_LIMITED
      if error.retry_after_s is not None:
        start = max(start, error.retry_after_s)
    elif isinstance(error, MalformedResponseError | UnexpectedStatusError):
      start, cap = BACKOFF_MALFORMED
    elif isinstance(error, VintedNetworkError):
      start, cap = BACKOFF_NETWORK
    else:
      start, cap = BACKOFF_NETWORK
    return min(cap, start * 2 ** (self._consecutive_errors - 1))
