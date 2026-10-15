"""Zweite, einmalige Probe: Länderfilter, Verkäuferdaten und Kauf-Ablauf.

Aufruf: python -m scripts.probe_extra "<Filter-Link>"

Requests (insgesamt 6, je 6 s Pause, ehrlicher User-Agent):
  1. Startseite                      -> anonyme Session
  2. GET /api/v2/countries           -> Länder-IDs (für country_ids)
  3. Katalog wie im Link             -> Referenz: total_entries + eine echte Verkäufer-ID
  4. Katalog + country_ids=<AT>      -> wirkt der serverseitige Länderfilter?
  5. GET /api/v2/users/<id>          -> liefert der Verkäufer-Endpunkt Land/Bewertung anonym?
  6. GET Artikelseite                -> wie sieht der Kauf-Link heute aus?

Personenbezogene Werte (Login, Name, Stadt) werden NICHT ausgegeben, nur Feldnamen
und Länder-/Bewertungsfelder.
"""

from __future__ import annotations

import re
import sys
import time
from typing import Any

import httpx

from scripts.probe import ANON_ID_HEADER, SESSION_COOKIE, api_headers
from vinted_bot.config import HONEST_USER_AGENT
from vinted_bot.vinted import endpoints
from vinted_bot.vinted.url_parser import parse_filter_url

PAUSE_S = 6


def main(argv: list[str]) -> int:
  parsed = parse_filter_url(argv[0])
  domain = parsed.domain
  locale = "de-AT"
  site = endpoints.site_root(domain).rstrip("/")

  with httpx.Client(timeout=15.0, follow_redirects=True) as client:
    home = client.get(
      site + "/",
      headers={"User-Agent": HONEST_USER_AGENT, "Accept": "text/html", "Accept-Language": locale},
    )
    token = client.cookies.get(SESSION_COOKIE)
    anon_id = home.headers.get(ANON_ID_HEADER)
    print(f"1 Startseite: {home.status_code}, Token {'ja' if token else 'NEIN'}")
    if not token:
      return 1
    headers = api_headers(HONEST_USER_AGENT, domain, locale)
    headers["Cookie"] = f"{SESSION_COOKIE}={token}"
    if anon_id:
      headers[ANON_ID_HEADER] = anon_id

    def get(label: str, url: str, params: dict[str, str] | None = None) -> httpx.Response:
      time.sleep(PAUSE_S)
      response = client.get(url, params=params, headers=headers)
      print(f"{label}: HTTP {response.status_code} ({response.headers.get('content-type', '-')})")
      return response

    # 2. Länder
    countries: list[dict[str, Any]] = []
    response = get("2 /api/v2/countries", site + "/api/v2/countries")
    if response.status_code == 200:
      data = response.json()
      countries = data.get("countries") or data.get("items") or []
      print(
        f"  {len(countries)} Länder, Felder: {sorted(countries[0].keys()) if countries else '-'}"
      )
      for country in countries:
        print(
          f"  id={country.get('id')} iso={country.get('iso_code') or country.get('code')} "
          f"name={country.get('title') or country.get('name')}"
        )

    # 3. Referenz-Katalog
    query = {**parsed.api_params, "page": "1", "per_page": "20"}
    response = get("3 Katalog (Referenz)", endpoints.catalog_url(domain), query)
    seller_id = None
    item_id = None
    if response.status_code == 200:
      data = response.json()
      print(f"  total_entries={data.get('pagination', {}).get('total_entries')}")
      if data.get("items"):
        seller_id = data["items"][0]["user"]["id"]
        item_id = data["items"][0]["id"]

    # 4. Katalog mit Länderfilter Österreich
    at_id = next(
      (
        c.get("id") for c in countries if (c.get("iso_code") or c.get("code") or "").upper() == "AT"
      ),
      None,
    )
    if at_id is not None:
      response = get(
        f"4 Katalog + country_ids={at_id} (AT)",
        endpoints.catalog_url(domain),
        {**query, "country_ids": str(at_id)},
      )
      if response.status_code == 200:
        print(f"  total_entries={response.json().get('pagination', {}).get('total_entries')}")
      else:
        print(f"  Antwort: {response.text[:200]!r}")
    else:
      print("4 übersprungen (keine AT-ID gefunden)")

    # 5. Verkäufer
    if seller_id is not None:
      response = get("5 /api/v2/users/<id>", f"{site}/api/v2/users/{seller_id}")
      if response.status_code == 200:
        user = response.json().get("user", {})
        print(f"  Felder: {', '.join(sorted(user.keys()))}")
        for key in (
          "country_id",
          "country_iso_code",
          "country_code",
          "feedback_reputation",
          "feedback_count",
          "positive_feedback_count",
          "business",
        ):
          print(f"  {key} = {user.get(key)!r}")
        print(f"  photo vorhanden: {bool(user.get('photo'))}")
      else:
        print(f"  Antwort: {response.text[:200]!r}")

    # 6. Artikelseite: alle Links/Pfade mit Kauf-Bezug suchen
    if item_id is not None:
      response = get("6 Artikelseite", endpoints.item_url(domain, item_id))
      html = response.text
      patterns = sorted(
        set(
          re.findall(r'(?:/transaction[^"\'\s\\<>]{0,80}|/checkout[^"\'\s\\<>]{0,80})', html)
          + re.findall(r'"(buy_[a-z_]{0,40}|[a-z_]{0,30}checkout[a-z_]{0,30})"', html)
        )
      )
      print(f"  Kauf-bezogene Treffer ({len(patterns)}):")
      for pattern in patterns[:40]:
        print(f"    {pattern}")
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
