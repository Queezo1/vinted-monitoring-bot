"""Verkäuferdaten (Land, Bewertung, Avatar) mit Cache.

Die Katalog-Suche liefert nur Verkäufer-ID und Login. Für Länderfilter, Flagge und Sterne
fragen wir /api/v2/users/{id} ab – aber nur für NEUE Artikel und höchstens einmal pro
Verkäufer in 30 Tagen (Leute ziehen selten in ein anderes Land).
"""

from __future__ import annotations

from datetime import timedelta

from vinted_bot.storage.repository import Repository
from vinted_bot.vinted.client import VintedClient
from vinted_bot.vinted.models import SellerInfo, parse_seller

SELLER_CACHE_MAX_AGE = timedelta(days=30)


class SellerResolver:
  def __init__(
    self, repo: Repository, client: VintedClient, max_age: timedelta = SELLER_CACHE_MAX_AGE
  ) -> None:
    self._repo = repo
    self._client = client
    self._max_age = max_age

  async def resolve(self, domain: str, user_id: int) -> SellerInfo | None:
    """Aus dem Cache oder per Request. VintedError-Ausnahmen gehen an den Aufrufer."""
    cached = await self._repo.get_seller(user_id, self._max_age)
    if cached is not None:
      return cached
    raw = await self._client.fetch_user(domain, user_id)
    seller = parse_seller(raw)
    if seller is not None:
      await self._repo.save_seller(seller)
    return seller
