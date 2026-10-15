"""Filter anlegen, ändern, pausieren, löschen – die Logik hinter den Slash-Commands.

Bewusst ohne Discord-Abhängigkeit: Die Commands (discord_bot/commands.py) kümmern sich nur
um Ein-/Ausgabe und Kanäle, alles andere passiert hier und ist so einfach testbar.
Fehler, die der Nutzer beheben kann, kommen als `UserError` mit deutscher Meldung.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from vinted_bot.config import MIN_INTERVAL_S
from vinted_bot.core.filter_manager import FilterManager
from vinted_bot.storage.repository import FilterRow, Repository
from vinted_bot.vinted.countries import InvalidCountryCodeError, parse_country_codes
from vinted_bot.vinted.url_parser import InvalidFilterUrlError, ParsedFilter, parse_filter_url

log = logging.getLogger(__name__)

MAX_INTERVAL_S = 3600


class UserError(Exception):
  """Fehler mit einer verständlichen Meldung für Discord."""


@dataclass(frozen=True)
class PreparedFilter:
  """Geprüfte Eingaben, bevor ein Kanal angelegt wird (damit kein verwaister Kanal entsteht)."""

  source_url: str
  parsed: ParsedFilter
  countries: tuple[str, ...]
  interval_s: int


class FilterService:
  def __init__(self, repo: Repository, manager: FilterManager, default_interval_s: int) -> None:
    self._repo = repo
    self._manager = manager
    self._default_interval_s = default_interval_s

  def prepare(
    self, link: str, countries_text: str | None, interval_s: int | None
  ) -> PreparedFilter:
    try:
      parsed = parse_filter_url(link)
    except InvalidFilterUrlError as error:
      raise UserError(f"Ungültiger Link: {error}") from error
    return PreparedFilter(
      source_url=link.strip(),
      parsed=parsed,
      countries=self._countries(countries_text),
      interval_s=self._interval(interval_s),
    )

  async def create(self, prepared: PreparedFilter, name: str, channel_id: int) -> FilterRow:
    existing = await self._repo.find_filter(
      prepared.parsed.canonical_url, prepared.countries, channel_id
    )
    if existing is not None:
      raise UserError(
        f"Diesen Filter gibt es in diesem Kanal schon (#{existing.id} {existing.name})."
      )
    filter_id = await self._repo.add_filter(
      name=name,
      source_url=prepared.source_url,
      canonical_url=prepared.parsed.canonical_url,
      domain=prepared.parsed.domain,
      api_params=prepared.parsed.api_params,
      channel_id=channel_id,
      countries=prepared.countries,
      interval_s=prepared.interval_s,
    )
    self._manager.start(filter_id)
    log.info("Filter %s (%s) angelegt", filter_id, name)
    return await self._get(filter_id)

  async def is_duplicate(self, prepared: PreparedFilter, channel_id: int) -> bool:
    found = await self._repo.find_filter(
      prepared.parsed.canonical_url, prepared.countries, channel_id
    )
    return found is not None

  async def pause(self, filter_id: int) -> FilterRow:
    flt = await self._get(filter_id)
    if flt.paused:
      raise UserError(f"#{flt.id} {flt.name} ist bereits pausiert.")
    await self._repo.set_paused(filter_id, True)
    await self._manager.stop(filter_id)
    return await self._get(filter_id)

  async def resume(self, filter_id: int) -> FilterRow:
    flt = await self._get(filter_id)
    if not flt.paused:
      raise UserError(f"#{flt.id} {flt.name} läuft bereits.")
    await self._repo.set_paused(filter_id, False)
    self._manager.start(filter_id)
    return await self._get(filter_id)

  async def remove(self, filter_id: int) -> tuple[FilterRow, bool]:
    """Löscht den Filter. Gibt zusätzlich zurück, ob sein Kanal jetzt ungenutzt ist."""
    flt = await self._get(filter_id)
    await self._manager.stop(filter_id)
    await self._repo.delete_filter(filter_id)
    channel_unused = not await self._repo.filters_in_channel(flt.channel_id)
    log.info("Filter %s (%s) gelöscht", filter_id, flt.name)
    return flt, channel_unused

  async def set_countries(self, filter_id: int, countries_text: str | None) -> FilterRow:
    await self._get(filter_id)
    await self._repo.set_countries(filter_id, self._countries(countries_text))
    # Kein Neustart nötig: der Poller liest den Filter vor jeder Abfrage neu
    return await self._get(filter_id)

  async def set_interval(self, filter_id: int, interval_s: int) -> FilterRow:
    await self._get(filter_id)
    await self._repo.set_interval(filter_id, self._interval(interval_s))
    return await self._get(filter_id)

  async def list(self) -> list[FilterRow]:
    return await self._repo.list_filters()

  # --- Interna ---------------------------------------------------------------

  async def _get(self, filter_id: int) -> FilterRow:
    flt = await self._repo.get_filter(filter_id)
    if flt is None:
      raise UserError(f"Filter #{filter_id} gibt es nicht. `/filter list` zeigt alle.")
    return flt

  def _interval(self, interval_s: int | None) -> int:
    value = self._default_interval_s if interval_s is None else interval_s
    if not MIN_INTERVAL_S <= value <= MAX_INTERVAL_S:
      raise UserError(f"Intervall muss zwischen {MIN_INTERVAL_S} und {MAX_INTERVAL_S} s liegen.")
    return value

  @staticmethod
  def _countries(text: str | None) -> tuple[str, ...]:
    try:
      return parse_country_codes(text)
    except InvalidCountryCodeError as error:
      raise UserError(str(error)) from error
