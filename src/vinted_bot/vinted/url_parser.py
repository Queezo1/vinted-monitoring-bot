"""Vinted-Filter-Link (aus der Adresszeile) -> Domain + API-Parameter + kanonische URL.

Beispiel-Eingabe:
  https://www.vinted.at/catalog?search_text=nike&catalog[]=1206&brand_ids[]=53&order=newest_first&time=1760...

Wichtig seit dem API-Umbau im September 2026: Die Such-API kennt viele Filter nur noch
unter `attribute_ids[...]`. Alte Namen wie `brand_ids` werden von der API NICHT abgelehnt,
sondern stillschweigend ignoriert – das Ergebnis wäre dann ungefiltert. Deshalb mappen wir
hier streng und melden alles, was wir nicht kennen, als `ignored` zurück.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse

SUPPORTED_DOMAINS: frozenset[str] = frozenset({"at", "de"})

# Browser-Parametername (ohne "[]") -> API-Parametername. Werte sind ID-Listen.
_LIST_PARAMS: dict[str, str] = {
  "catalog": "attribute_ids[catalog]",
  "catalog_ids": "attribute_ids[catalog]",
  "brand_ids": "attribute_ids[brand]",
  "size_ids": "attribute_ids[size]",
  "status_ids": "attribute_ids[status]",
  "color_ids": "attribute_ids[color]",
  "material_ids": "attribute_ids[material]",
  # Diese beiden behalten laut Live-Tests anderer Projekte ihre alten Namen
  "country_ids": "country_ids",
  "city_ids": "city_ids",
}

# Einzelwerte, die 1:1 übernommen werden
_SCALAR_PARAMS: frozenset[str] = frozenset({"search_text", "price_from", "price_to", "currency"})

# Parameter, die die Webseite für eigene Zwecke anhängt. Sie ändern sich bei jedem
# Seitenaufruf und gehören weder in die API-Anfrage noch in die kanonische URL.
_VOLATILE_PARAMS: frozenset[str] = frozenset(
  {
    "order",  # erzwingen wir selbst
    "time",
    "search_id",
    "search_by_image_uuid",
    "page",
    "per_page",
    "disabled_personalization",
    "referrer",
    "utm_source",
    "utm_medium",
    "utm_campaign",
  }
)

# Kategorie- bzw. Markenseiten tragen den Filter im Pfad, z. B. /catalog/1206-herren-schuhe
_CATALOG_PATH = re.compile(r"^/catalog/(\d+)")
_BRAND_PATH = re.compile(r"^/brand/(\d+)")
_DIGITS = re.compile(r"^\d+$")
_PRICE = re.compile(r"^\d+([.,]\d{1,2})?$")


class InvalidFilterUrlError(ValueError):
  """Der eingefügte Text ist kein verwendbarer Vinted-Filter-Link."""


@dataclass(frozen=True)
class ParsedFilter:
  domain: str  # "at" oder "de"
  api_params: dict[str, str]  # fertig für die Katalog-API (ohne page/per_page)
  canonical_url: str  # stabil, dient als Eindeutigkeits-Schlüssel in der DB
  ignored: tuple[str, ...]  # unbekannte Parameter -> Hinweis an den Nutzer


def extract_domain(url: str) -> str:
  """Gibt die Länderendung zurück ("at"/"de") oder wirft InvalidFilterUrlError."""
  host = (urlparse(url).hostname or "").lower()
  host = host.removeprefix("www.")
  if not host.startswith("vinted."):
    raise InvalidFilterUrlError(f"Das ist keine Vinted-Adresse: {host or url!r}")
  domain = host.removeprefix("vinted.")
  if domain not in SUPPORTED_DOMAINS:
    supported = ", ".join(f"vinted.{d}" for d in sorted(SUPPORTED_DOMAINS))
    raise InvalidFilterUrlError(f"vinted.{domain} wird nicht unterstützt (nur {supported})")
  return domain


def parse_filter_url(raw_url: str) -> ParsedFilter:
  url = raw_url.strip()
  if not url:
    raise InvalidFilterUrlError("Kein Link angegeben")
  if not url.startswith(("http://", "https://")):
    url = "https://" + url

  domain = extract_domain(url)
  parsed = urlparse(url)

  # Gesammelte ID-Listen je API-Parameter. dict als "geordnete Menge" gegen Duplikate.
  lists: dict[str, dict[str, None]] = {}
  scalars: dict[str, str] = {}
  ignored: list[str] = []

  def add_ids(api_key: str, value: str, source_key: str) -> None:
    # Manche Links enthalten kommagetrennte Listen in einem Wert
    for part in value.split(","):
      part = part.strip()
      if not part:
        continue
      if not _DIGITS.match(part):
        ignored.append(f"{source_key}={part}")
        continue
      lists.setdefault(api_key, {})[part] = None

  if match := _CATALOG_PATH.match(parsed.path):
    add_ids("attribute_ids[catalog]", match.group(1), "path")
  if match := _BRAND_PATH.match(parsed.path):
    add_ids("attribute_ids[brand]", match.group(1), "path")

  # keep_blank_values=False: leere Werte fallen weg (die API antwortet sonst mit 400)
  for raw_key, value in parse_qsl(parsed.query, keep_blank_values=False):
    key = raw_key.removesuffix("[]")
    value = value.strip()
    if not value or key in _VOLATILE_PARAMS:
      continue
    if key in _SCALAR_PARAMS:
      if key in ("price_from", "price_to"):
        if not _PRICE.match(value):
          ignored.append(f"{key}={value}")
          continue
        value = value.replace(",", ".")
      scalars[key] = value
    elif key in _LIST_PARAMS:
      add_ids(_LIST_PARAMS[key], value, key)
    else:
      ignored.append(f"{key}={value}")

  if not lists and not scalars.keys() - {"currency"}:
    raise InvalidFilterUrlError(
      "Der Link enthält keine Filter. Stelle die Suche auf Vinted ein und kopiere die "
      "Adresszeile, sobald Ergebnisse angezeigt werden."
    )

  api_params: dict[str, str] = dict(scalars)
  for api_key, ids in lists.items():
    # Sortiert, damit dieselbe Suche immer dieselbe kanonische URL ergibt
    api_params[api_key] = ",".join(sorted(ids, key=int))
  api_params["order"] = "newest_first"

  return ParsedFilter(
    domain=domain,
    api_params=api_params,
    canonical_url=_canonical_url(domain, api_params),
    ignored=tuple(ignored),
  )


def _canonical_url(domain: str, api_params: dict[str, str]) -> str:
  # safe: Leerzeichen als "+", Kommas und eckige Klammern lesbar lassen
  query = urlencode(sorted(api_params.items()), safe=",[]")
  return f"https://www.vinted.{domain}/catalog?{query}"
