"""Der einzige Ort, der HTTP-Anfragen an Vinted schickt.

Ablauf (per M0-Probe am 15.10.2026 bestätigt):
  1. GET https://www.vinted.<tld>/  -> Cookie `access_token_web` + Header `X-Anon-Id`
  2. GET https://api.vinted.<tld>/svc-catalogue/items  mit diesem Cookie
  3. GET https://www.vinted.<tld>/api/v2/users/<id>    (Verkäufer: Land, Bewertung)

Leitplanken: ehrlicher User-Agent aus der Konfiguration, keine Browser-Imitations-Header,
kein Proxy, jeder Request geht durch den globalen Rate-Limiter. Der einzige automatische
Wiederholungsversuch ist bei 401 (abgelaufene Session) – genau einmal.

Priorität: Katalog und Länder-Lookups (entscheiden, OB gepostet wird) sind "hoch".
Fotos/Beschreibung nachladen ist "niedrig" und wartet, solange hohe Anfragen anstehen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from vinted_bot.vinted import endpoints
from vinted_bot.vinted.errors import (
  BlockedError,
  MalformedResponseError,
  RateLimitedError,
  UnauthorizedError,
  UnexpectedStatusError,
  VintedNetworkError,
)
from vinted_bot.vinted.rate_limiter import GlobalRateLimiter
from vinted_bot.vinted.stats import RequestStats

log = logging.getLogger(__name__)

SESSION_COOKIE = "access_token_web"
ANON_ID_HEADER = "X-Anon-Id"
PER_PAGE = 20
# Schutz vor riesigen Antworten (Artikelseite ~2 MB, Katalog ~40 KB)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

_LOCALES = {"at": "de-AT", "de": "de-DE"}


@dataclass(frozen=True)
class VintedSession:
  token: str
  anon_id: str | None


class VintedClient:
  def __init__(
    self,
    http: httpx.AsyncClient,
    limiter: GlobalRateLimiter,
    user_agent: str,
    stats: RequestStats | None = None,
  ) -> None:
    self._http = http
    self._limiter = limiter
    self._user_agent = user_agent
    self.stats = stats or RequestStats()
    # Eine anonyme Session pro Domain, wiederverwendet bis Vinted 401 sagt
    self._sessions: dict[str, VintedSession] = {}

  # --- Öffentliche Methoden ------------------------------------------------

  async def fetch_catalog(self, domain: str, params: dict[str, str]) -> list[dict[str, Any]]:
    """Seite 1 der Suche, neueste zuerst. Gibt die rohen Artikel-Dicts zurück."""
    query = {**params, "order": "newest_first", "page": "1", "per_page": str(PER_PAGE)}
    data = await self._get_json(domain, "catalog", endpoints.catalog_url(domain), query)
    items = data.get("items")
    if not isinstance(items, list):
      raise MalformedResponseError(f"Katalog ohne items-Liste, Schlüssel: {list(data)[:10]}")
    return [item for item in items if isinstance(item, dict)]

  async def fetch_user(
    self, domain: str, user_id: int, *, low_priority: bool = False
  ) -> dict[str, Any]:
    """Öffentliches Verkäuferprofil (rohes Dict, enthält mehr als wir speichern!)."""
    url = endpoints.user_url(domain, user_id)
    return await self._get_json(domain, "user", url, None, low_priority=low_priority)

  async def fetch_item_page(self, domain: str, item_id: int, *, low_priority: bool = True) -> str:
    """Öffentliche Artikelseite als HTML (für alle Fotos und die Beschreibung)."""
    response = await self._send(
      "item_page",
      endpoints.item_url(domain, item_id),
      None,
      self._page_headers(domain),
      low_priority=low_priority,
    )
    self._raise_for_status(response, f"Artikelseite {item_id}")
    return response.text

  # --- Interna -------------------------------------------------------------

  async def _send(
    self,
    kind: str,
    url: str,
    params: dict[str, str] | None,
    headers: dict[str, str],
    *,
    low_priority: bool = False,
  ) -> httpx.Response:
    """JEDER Request an Vinted läuft hier durch: Rate-Limiter, Größenlimit, Statistik."""
    await self._limiter.acquire(low_priority=low_priority)
    try:
      # Gestreamt lesen, damit eine (fehlerhafte) Riesen-Antwort nicht den Speicher füllt
      async with self._http.stream("GET", url, params=params, headers=headers) as streamed:
        body = bytearray()
        async for chunk in streamed.aiter_bytes():
          body += chunk
          if len(body) > MAX_RESPONSE_BYTES:
            self.stats.record(kind, "too_large")
            raise MalformedResponseError(f"{kind}: Antwort größer als {MAX_RESPONSE_BYTES} Bytes")
        # Fertige Antwort mit dem gelesenen Inhalt bauen (für .json(), .text, .cookies).
        # aiter_bytes() liefert bereits ENTPACKTE Daten -> Content-Encoding/-Length entfernen,
        # sonst würde httpx ein zweites Mal entpacken (DecodingError).
        headers = httpx.Headers(streamed.headers)
        for name in ("content-encoding", "content-length"):
          headers.pop(name, None)
        response = httpx.Response(
          streamed.status_code,
          headers=headers,
          content=bytes(body),
          request=streamed.request,
        )
    except httpx.HTTPError as error:
      self.stats.record(kind, "network")
      raise VintedNetworkError(f"{kind}: {error!r}") from error
    self.stats.record(kind, str(response.status_code))
    return response

  async def _get_json(
    self,
    domain: str,
    kind: str,
    url: str,
    params: dict[str, str] | None,
    *,
    low_priority: bool = False,
  ) -> dict[str, Any]:
    session = await self._session(domain)
    try:
      return await self._request_json(domain, kind, url, params, session, low_priority)
    except UnauthorizedError:
      # Token abgelaufen: Session verwerfen, genau EIN neuer Versuch
      log.info("vinted.%s: Session abgelaufen (401), hole neue", domain)
      self._sessions.pop(domain, None)
      session = await self._session(domain)
      return await self._request_json(domain, kind, url, params, session, low_priority)

  async def _session(self, domain: str) -> VintedSession:
    if domain in self._sessions:
      return self._sessions[domain]
    response = await self._send(
      "session", endpoints.site_root(domain), None, self._page_headers(domain)
    )
    self._raise_for_status(response, f"Startseite vinted.{domain}")
    token = response.cookies.get(SESSION_COOKIE)
    if not token:
      # Kein Token trotz 200 deutet auf eine Challenge-Seite hin
      raise BlockedError(f"vinted.{domain}: Startseite ohne {SESSION_COOKIE}-Cookie")
    anon_id = (response.headers.get(ANON_ID_HEADER) or "").strip() or None
    session = VintedSession(token=token, anon_id=anon_id)
    self._sessions[domain] = session
    log.info("vinted.%s: neue anonyme Session geholt", domain)
    return session

  async def _request_json(
    self,
    domain: str,
    kind: str,
    url: str,
    params: dict[str, str] | None,
    session: VintedSession,
    low_priority: bool = False,
  ) -> dict[str, Any]:
    site = endpoints.site_root(domain).rstrip("/")
    locale = _LOCALES.get(domain, "de-AT")
    headers = {
      "User-Agent": self._user_agent,
      "Accept": "application/json, text/plain, */*",
      "Accept-Language": self._accept_language(domain),
      "Locale": locale,
      "Origin": site,
      "Referer": site + "/",
      # Cookie explizit setzen: api.- und www.-Host teilen sich sonst nicht zuverlässig den Jar
      "Cookie": f"{SESSION_COOKIE}={session.token}",
    }
    if session.anon_id:
      headers[ANON_ID_HEADER] = session.anon_id

    response = await self._send(kind, url, params, headers, low_priority=low_priority)
    self._raise_for_status(response, url)
    try:
      data = response.json()
    except ValueError as error:
      raise MalformedResponseError(f"{url}: kein JSON, Anfang: {response.text[:120]!r}") from error
    if not isinstance(data, dict):
      raise MalformedResponseError(f"{url}: JSON ist kein Objekt")
    return data

  @staticmethod
  def _raise_for_status(response: httpx.Response, what: str) -> None:
    status = response.status_code
    if status == 200:
      return
    if status == 401:
      raise UnauthorizedError(f"{what}: 401")
    if status == 403:
      raise BlockedError(f"{what}: 403 (abgewiesen)")
    if status == 429:
      raise RateLimitedError(f"{what}: 429", _retry_after_s(response))
    raise UnexpectedStatusError(f"{what}: HTTP {status}, {response.text[:120]!r}", status)

  def _page_headers(self, domain: str) -> dict[str, str]:
    """Header für normale HTML-Seiten (Startseite, Artikelseite)."""
    return {
      "User-Agent": self._user_agent,
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": self._accept_language(domain),
    }

  @staticmethod
  def _accept_language(domain: str) -> str:
    return f"{_LOCALES.get(domain, 'de-AT')},de;q=0.8,en;q=0.5"


def _retry_after_s(response: httpx.Response) -> float | None:
  value = response.headers.get("Retry-After")
  if value is None:
    return None
  try:
    return max(0.0, float(value))
  except ValueError:
    return None  # HTTP-Datumsformat ignorieren wir, dann gilt unser eigener Backoff
