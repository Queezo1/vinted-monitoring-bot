"""Die Abfrageschleife für EINEN Filter. Jeder Filter läuft als eigene asyncio-Task.

Ein Durchlauf (`poll_once`):
  Katalog holen -> parsen -> Dedupe -> (nur bei Länderfilter: Verkäufer nachschlagen)
  -> Ergebnis in EINER Transaktion speichern -> Dispatcher wecken.
Fotos, Beschreibung und (ohne Länderfilter) Flagge/Bewertung lädt der Enricher NACH dem
Posten nach – so steht ein neuer Artikel so schnell wie möglich in Discord.
`poll_once` gibt zurück, wie lange bis zum nächsten Durchlauf gewartet werden soll.

403/429 regelt der DomainGuard für alle Filter einer Domain gemeinsam (siehe domain_guard.py).
Andere Fehler (Netzwerk, geändertes Antwortformat) haben einen eigenen Backoff pro Filter.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime

from vinted_bot.core.dedupe import select_new
from vinted_bot.core.domain_guard import DomainBlock, DomainGuard
from vinted_bot.core.notifications import (
  Notifier,
  NullNotifier,
  Severity,
  StatusEvent,
  quote_error,
)
from vinted_bot.core.seller_lookup import SellerResolver
from vinted_bot.storage.repository import FilterRow, NewItem, Repository, utc_now
from vinted_bot.vinted.client import VintedClient
from vinted_bot.vinted.errors import (
  BlockedError,
  MalformedResponseError,
  RateLimitedError,
  UnexpectedStatusError,
  VintedError,
  VintedNetworkError,
)
from vinted_bot.vinted.models import SellerInfo, parse_item

log = logging.getLogger(__name__)

JITTER = 0.15  # ±15 % Streuung, damit nicht alle Filter im Gleichschritt abfragen

# Backoff-Stufen je Fehlerart: (Start, Maximum) in Sekunden. Verdoppelt sich je Fehler in Folge.
BACKOFF_NETWORK = (10, 5 * 60)
BACKOFF_MALFORMED = (10 * 60, 60 * 60)
# Ab so vielen Fehlern in Folge gibt es eine Meldung im Admin-Kanal
FAILING_THRESHOLD = 3
# Schnelle Filter brauchen weniger Artikel pro Abfrage: In ≤ 10 s kommen nie 10 neue dazu.
# Kleinere Antwort = etwas schneller und weniger Last für Vinted.
FAST_INTERVAL_S = 10
FAST_PER_PAGE = 10


class FilterPoller:
  def __init__(
    self,
    filter_id: int,
    *,
    repo: Repository,
    client: VintedClient,
    sellers: SellerResolver,
    on_new_items: Callable[[], None],
    interval_for: Callable[[FilterRow], float] | None = None,
    on_throttled: Callable[[], None] | None = None,
    guard: DomainGuard | None = None,
    notifier: Notifier | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[], float] = random.random,
    now: Callable[[], datetime] = utc_now,
  ) -> None:
    self.filter_id = filter_id
    self._repo = repo
    self._client = client
    self._sellers = sellers
    self._on_new_items = on_new_items
    # Effektives Intervall (z. B. gestreckt bei vielen Filtern); Standard: das eingestellte
    self._interval_for = interval_for or (lambda flt: flt.interval_s)
    self._on_throttled = on_throttled or (lambda: None)
    self._guard = guard or DomainGuard(now)
    self._notifier = notifier or NullNotifier()
    self._now = now
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
      # Ist die Domain gesperrt, wird gar nicht erst angefragt
      until = self._guard.blocked_until(flt.domain)
      if until is not None:
        await self._sleep(self._seconds_until(until) + 10 * self._rng())
        continue
      delay_s = await self.poll_once(flt)
      await self._sleep(delay_s)

  async def poll_once(self, flt: FilterRow) -> float:
    try:
      await self._poll(flt)
    except (BlockedError, RateLimitedError) as error:
      # Gilt für die ganze Domain: alle Filter pausieren gemeinsam
      already_blocked = self._guard.blocked_until(flt.domain) is not None
      block = self._guard.report_error(flt.domain, error)
      self._on_throttled()  # globalen Takt verlangsamen
      await self._repo.record_poll_error(flt.id, f"{type(error).__name__}: {error}")
      log.warning(
        "vinted.%s gesperrt (%s, Stufe %s) bis %s – ausgelöst von Filter %s: %s",
        flt.domain,
        block.reason,
        block.level,
        block.until.isoformat(timespec="seconds"),
        flt.id,
        error,
      )
      if not already_blocked:
        self._notifier.post(_block_event(block))
      return self._seconds_until(block.until)
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
      if fail_count == FAILING_THRESHOLD:
        self._notifier.post(
          StatusEvent(
            Severity.WARNING,
            f"Filter #{flt.id} {flt.name}: wiederholte Fehler",
            f"{fail_count} Fehler in Folge. Letzter: "
            f"{quote_error(f'{type(error).__name__}: {error}')}\n"
            f"Nächster Versuch in {delay_s / 60:.0f} min. Bei `MalformedResponseError` hat "
            "Vinted vermutlich die API geändert.",
            key=f"failing:{flt.id}",
          )
        )
      return delay_s

    self._consecutive_errors = 0
    recovery = self._guard.report_success(flt.domain)
    if recovery is not None:
      self._notifier.post(
        StatusEvent(
          Severity.INFO,
          f"vinted.{flt.domain} wieder erreichbar",
          f"Abfragen laufen wieder. Gestört seit <t:{int(recovery.since.timestamp())}:R>.",
          key=f"recovered:{flt.domain}:{int(recovery.since.timestamp())}",
        )
      )
    elif flt.fail_count >= FAILING_THRESHOLD:
      self._notifier.post(
        StatusEvent(
          Severity.INFO,
          f"Filter #{flt.id} {flt.name} läuft wieder",
          f"Nach {flt.fail_count} Fehlern in Folge war die Abfrage wieder erfolgreich.",
          key=f"filter-ok:{flt.id}:{flt.fail_count}",
        )
      )
    return self._jittered(self._interval_for(flt))

  async def _poll(self, flt: FilterRow) -> None:
    if flt.interval_s <= FAST_INTERVAL_S and flt.baseline_done:
      raw_items = await self._client.fetch_catalog(
        flt.domain, flt.api_params, per_page=FAST_PER_PAGE
      )
    else:
      raw_items = await self._client.fetch_catalog(flt.domain, flt.api_params)
    items = [item for raw in raw_items if (item := parse_item(raw, flt.domain)) is not None]
    if len(items) < len(raw_items):
      log.warning("Filter %s: %s Artikel nicht lesbar", flt.id, len(raw_items) - len(items))

    seen = await self._repo.seen_item_ids(flt.id)
    result = select_new(items, seen, baseline_done=flt.baseline_done, max_seen_id=flt.max_seen_id)

    new_items: list[NewItem] = []
    skipped = list(result.skipped_ids)
    # Vor dem Posten nur nachschlagen, wenn der Länderfilter es verlangt (sonst Enricher)
    lookups_allowed = bool(flt.countries)
    for item in result.new_items:
      seller: SellerInfo | None = None
      if item.seller_id is not None:
        # Aus dem Cache kostet es nichts – dann gleich mit Flagge/Sternen posten
        seller = await self._sellers.cached(item.seller_id)
      if seller is None and lookups_allowed and item.seller_id is not None:
        try:
          seller = await self._sellers.resolve(flt.domain, item.seller_id)
        except (BlockedError, RateLimitedError) as error:
          # Nicht weiter nachschlagen in diesem Durchlauf – die restlichen Artikel gehen
          # ohne Land/Bewertung raus, statt Vinted weiter zu bedrängen.
          log.warning("Verkäufer-Lookup gestoppt: %s", error)
          lookups_allowed = False
          self._on_throttled()
          self._post_lookup_blocked(flt.domain, "Verkäufer-Abfrage", error)
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

  def _post_lookup_blocked(self, domain: str, what: str, error: VintedError) -> None:
    self._notifier.post(
      StatusEvent(
        Severity.WARNING,
        f"{what} auf vinted.{domain} abgewiesen",
        f"{quote_error(error)} – Artikel werden vorerst ohne diese Zusatzinfos gepostet "
        "(keine Flagge/Bewertung bzw. nur 1 Foto). Der Katalog selbst läuft weiter.",
        key=f"lookup:{domain}:{what}",
      )
    )

  def _seconds_until(self, moment: datetime) -> float:
    return max(1.0, (moment - self._now()).total_seconds())

  def _jittered(self, interval_s: float) -> float:
    return interval_s * (1 + JITTER * (2 * self._rng() - 1))

  def _error_delay(self, error: VintedError) -> float:
    if isinstance(error, MalformedResponseError | UnexpectedStatusError):
      start, cap = BACKOFF_MALFORMED
    elif isinstance(error, VintedNetworkError):
      start, cap = BACKOFF_NETWORK
    else:
      start, cap = BACKOFF_NETWORK
    return min(cap, start * 2 ** (self._consecutive_errors - 1))


def _block_event(block: DomainBlock) -> StatusEvent:
  until = f"<t:{int(block.until.timestamp())}:R>"
  if block.reason == "rate_limited":
    return StatusEvent(
      Severity.WARNING,
      f"vinted.{block.domain}: zu viele Anfragen (429)",
      f"Alle Filter dieser Domain pausieren, nächster Versuch {until} (Stufe {block.level}).\n"
      "Wenn das öfter passiert: Intervalle erhöhen oder weniger Filter.",
      key=f"block:{block.domain}:{block.reason}:{block.level}",
    )
  return StatusEvent(
    Severity.ERROR,
    f"vinted.{block.domain}: Zugriff verweigert (403)",
    "Vinted weist Anfragen von deiner IP ab. Alle Filter dieser Domain pausieren, "
    f"nächster Versuch {until} (Stufe {block.level}, steigt bis max. 6 h).\n"
    "Der Bot umgeht Sperren bewusst nicht. Bleibt es dabei: Intervalle erhöhen oder pausieren.",
    key=f"block:{block.domain}:{block.reason}:{block.level}",
  )
