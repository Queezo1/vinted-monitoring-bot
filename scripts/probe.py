"""M0-Live-Probe: prüft EINMALIG und MANUELL, ob und wie die Vinted-Katalogsuche antwortet.

Aufruf (aus dem Projektordner, mit aktivierter venv):
  python -m scripts.probe "https://www.vinted.at/catalog?search_text=nike&..."
  python -m scripts.probe "<link>" --dry-run          # nur Link-Mapping anzeigen, kein Netzwerk
  python -m scripts.probe "<link>" --ua honest        # nur ehrlichen User-Agent testen
  python -m scripts.probe "<link>" --browser-ua "..." # eigenen Browser-UA verwenden

Was passiert pro User-Agent-Variante (max. 3 Requests, mit Pausen dazwischen):
  1. GET Startseite                -> anonymes Cookie `access_token_web` + Header `X-Anon-Id`
  2. GET api.../svc-catalogue/items -> Seite 1, neueste zuerst, 20 Artikel
  3. nur bei 401: dieselbe Anfrage einmal mit `Authorization: Bearer` statt Cookie

Bewusst NICHT enthalten (Leitplanken): Browser-Imitations-Header (Sec-Fetch-*, DNT ...),
TLS-Fingerprint-Imitation, Proxys, Retry-Schleifen. Beide Varianten unterscheiden sich
ausschließlich im User-Agent, damit das Ergebnis eindeutig ist.

Es werden KEINE Token-Werte ausgegeben. Bei Erfolg wird die Antwort anonymisiert
(Verkäuferdaten und Foto-URLs ersetzt) als Test-Fixture gespeichert.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from vinted_bot.config import HONEST_USER_AGENT
from vinted_bot.vinted import endpoints
from vinted_bot.vinted.url_parser import InvalidFilterUrlError, parse_filter_url

# Fester Browser-UA (Firefox auf Windows). Besser: mit --browser-ua den UA deines eigenen
# Browsers übergeben (in der Browser-Konsole: navigator.userAgent).
DEFAULT_BROWSER_UA = (
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"
)

SESSION_COOKIE = "access_token_web"
ANON_ID_HEADER = "X-Anon-Id"
PAUSE_BETWEEN_REQUESTS_S = 6
PAUSE_BETWEEN_VARIANTS_S = 15
FIXTURE_DIR = Path("tests/fixtures")

# Felder aus PLAN.md Abschnitt 4, deren Vorhandensein wir prüfen wollen
FIELDS_OF_INTEREST = [
  "id",
  "title",
  "url",
  "path",
  "price.amount",
  "price.currency_code",
  "total_item_price.amount",
  "service_fee.amount",
  "brand_title",
  "size_title",
  "status",
  "item_box.first_line",
  "item_box.second_line",
  "item_box.accessibility_label",
  "photo.url",
  "photo.full_size_url",
  "photo.high_resolution.timestamp",
  "photos",
  "user.id",
  "user.login",
  "user.profile_url",
  "user.photo.url",
  "user.feedback_reputation",
  "user.feedback_count",
  "user.country_code",
  "user.country_iso_code",
  "country_code",
  "promoted",
  "favourite_count",
  "view_count",
  "description",
  "created_at_ts",
]


@dataclass
class VariantResult:
  name: str
  user_agent: str
  steps: list[str] = field(default_factory=list)
  ok: bool = False
  catalog: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Reine Hilfsfunktionen (getestet in tests/test_probe.py)
# ---------------------------------------------------------------------------


def get_path(data: Any, path: str) -> Any:
  """Liest verschachtelte Werte per Punkt-Pfad, z. B. "user.photo.url". None wenn fehlend."""
  current = data
  for part in path.split("."):
    if not isinstance(current, dict) or part not in current:
      return None
    current = current[part]
  return current


def field_presence(items: list[dict[str, Any]], paths: list[str]) -> dict[str, int]:
  """Zählt pro Pfad, in wie vielen Artikeln der Wert vorhanden (nicht None/leer) ist."""
  counts: dict[str, int] = {}
  for path in paths:
    counts[path] = sum(1 for item in items if get_path(item, path) not in (None, "", [], {}))
  return counts


def flatten_keys(data: Any, prefix: str = "") -> list[str]:
  """Alle Schlüsselpfade eines JSON-Objekts (Listen: nur erstes Element, als [0])."""
  keys: list[str] = []
  if isinstance(data, dict):
    for key, value in data.items():
      path = f"{prefix}.{key}" if prefix else key
      keys.append(path)
      keys.extend(flatten_keys(value, path))
  elif isinstance(data, list) and data:
    keys.extend(flatten_keys(data[0], f"{prefix}[0]"))
  return keys


def anonymize_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
  """Ersetzt personenbezogene Daten und Foto-URLs durch Platzhalter. Struktur bleibt gleich.

  Verkäufer werden konsistent durchnummeriert (gleicher Verkäufer -> gleicher Platzhalter).
  """
  result = copy.deepcopy(catalog)
  seller_numbers: dict[Any, int] = {}

  def fake_url(kind: str, n: int) -> str:
    return f"https://images.example.invalid/{kind}/{n}.jpg"

  def scrub_photo(photo: Any, kind: str, n: int) -> None:
    if not isinstance(photo, dict):
      return
    for key in ("url", "full_size_url"):
      if key in photo:
        photo[key] = fake_url(kind, n)
    for thumb in photo.get("thumbnails") or []:
      if isinstance(thumb, dict) and "url" in thumb:
        thumb["url"] = fake_url(f"{kind}-thumb", n)

  for index, item in enumerate(result.get("items") or [], start=1):
    if not isinstance(item, dict):
      continue
    user = item.get("user")
    if isinstance(user, dict):
      n = seller_numbers.setdefault(user.get("id"), len(seller_numbers) + 1)
      user["id"] = 900000 + n
      user["login"] = f"seller_{n}"
      if "profile_url" in user:
        user["profile_url"] = f"https://www.vinted.example.invalid/member/{900000 + n}"
      scrub_photo(user.get("photo"), "avatar", n)
    scrub_photo(item.get("photo"), "item", index)
    for photo_no, photo in enumerate(item.get("photos") or [], start=1):
      scrub_photo(photo, f"item{index}", photo_no)
    # Suchinterna sind für uns irrelevant und blähen die Fixture nur auf
    item.pop("search_tracking_params", None)
  return result


def describe_block_signals(response: httpx.Response) -> list[str]:
  """Erkennt typische Zeichen von Bot-Schutz in einer Antwort."""
  signals: list[str] = []
  if "cf-ray" in response.headers:
    signals.append("Cloudflare (cf-ray)")
  if response.headers.get("cf-mitigated"):
    signals.append(f"Cloudflare-Challenge (cf-mitigated={response.headers['cf-mitigated']})")
  set_cookies = " ".join(response.headers.get_list("set-cookie")).lower()
  if "datadome=" in set_cookies or "x-datadome" in response.headers:
    signals.append("DataDome")
  body_start = response.text[:2000].lower() if response.content else ""
  if "captcha" in body_start or "challenge" in body_start:
    signals.append("Captcha/Challenge im Body")
  return signals


# ---------------------------------------------------------------------------
# Netzwerk-Teil
# ---------------------------------------------------------------------------


def api_headers(user_agent: str, domain: str, locale: str) -> dict[str, str]:
  """Funktionale Header, wie sie die Webseite an ihre eigene API schickt (ohne Sec-Fetch-*)."""
  site = endpoints.site_root(domain).rstrip("/")
  return {
    "User-Agent": user_agent,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": f"{locale},de;q=0.8,en;q=0.5",
    "Locale": locale,
    "Origin": site,
    "Referer": site + "/",
  }


def run_variant(name: str, user_agent: str, domain: str, params: dict[str, str]) -> VariantResult:
  result = VariantResult(name=name, user_agent=user_agent)
  locale = "de-AT" if domain == "at" else "de-DE"

  def log(line: str) -> None:
    result.steps.append(line)
    print(f"  [{name}] {line}")

  with httpx.Client(timeout=15.0, follow_redirects=True) as client:
    # --- Schritt 1: Startseite (Session) ---
    try:
      home = client.get(
        endpoints.site_root(domain),
        headers={
          "User-Agent": user_agent,
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": f"{locale},de;q=0.8,en;q=0.5",
        },
      )
    except httpx.HTTPError as error:
      log(f"Startseite: Netzwerkfehler {type(error).__name__}: {error}")
      return result

    cookie_names = sorted({cookie.name for cookie in client.cookies.jar})
    token = client.cookies.get(SESSION_COOKIE)
    anon_id = (home.headers.get(ANON_ID_HEADER) or "").strip() or None
    log(f"Startseite: HTTP {home.status_code}, Ziel {home.url}")
    log(f"  Cookies (nur Namen): {', '.join(cookie_names) or '-'}")
    log(
      f"  {SESSION_COOKIE}: {'vorhanden' if token else 'FEHLT'}; {ANON_ID_HEADER}: "
      f"{'vorhanden' if anon_id else 'fehlt'}"
    )
    if signals := describe_block_signals(home):
      log(f"  Schutz-Signale: {', '.join(signals)}")
    if home.status_code != 200 or not token:
      log("=> Keine Session erhalten. Abbruch dieser Variante (kein weiterer Versuch).")
      return result

    # --- Schritt 2: Katalog ---
    time.sleep(PAUSE_BETWEEN_REQUESTS_S)
    headers = api_headers(user_agent, domain, locale)
    if anon_id:
      headers[ANON_ID_HEADER] = anon_id
    query = {**params, "page": "1", "per_page": "20"}

    # Cookie explizit setzen (wie vinted_scraper) statt dem Cookie-Jar die Domainwahl zu überlassen
    catalog_response = _get_catalog(
      client, domain, query, {**headers, "Cookie": f"{SESSION_COOKIE}={token}"}, log
    )
    if catalog_response is None:
      return result

    # --- Schritt 3: nur bei 401 einmal mit Bearer-Token ---
    if catalog_response.status_code == 401:
      log("401 mit Cookie -> einmaliger Versuch mit Authorization: Bearer")
      time.sleep(PAUSE_BETWEEN_REQUESTS_S)
      catalog_response = _get_catalog(
        client, domain, query, {**headers, "Authorization": f"Bearer {token}"}, log
      )
      if catalog_response is None:
        return result

    status = catalog_response.status_code
    body_start = catalog_response.text[:300]
    if status == 429:
      retry_after = catalog_response.headers.get("Retry-After", "-")
      log(f"=> 429 zu viele Anfragen (Retry-After: {retry_after})")
      return result
    if status != 200:
      log(f"=> HTTP {status}. Antwortanfang: {body_start!r}")
      return result
    try:
      data = catalog_response.json()
    except ValueError:
      log(f"=> 200, aber kein JSON (vermutlich Challenge-Seite). Anfang: {body_start!r}")
      return result
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
      shape = list(data)[:15] if isinstance(data, dict) else type(data).__name__
      log(f"=> 200, aber keine items-Liste. Top-Level: {shape}")
      return result

    log(f"=> OK: {len(data['items'])} Artikel. Top-Level-Schlüssel: {', '.join(data.keys())}")
    result.ok = True
    result.catalog = data
    return result


def _get_catalog(
  client: httpx.Client, domain: str, query: dict[str, str], headers: dict[str, str], log
) -> httpx.Response | None:
  try:
    response = client.get(endpoints.catalog_url(domain), params=query, headers=headers)
  except httpx.HTTPError as error:
    log(f"Katalog: Netzwerkfehler {type(error).__name__}: {error}")
    return None
  log(f"Katalog: HTTP {response.status_code} ({response.headers.get('content-type', '-')})")
  if signals := describe_block_signals(response):
    log(f"  Schutz-Signale: {', '.join(signals)}")
  return response


# ---------------------------------------------------------------------------
# Bericht
# ---------------------------------------------------------------------------


def print_field_report(catalog: dict[str, Any], domain: str) -> None:
  items = [item for item in catalog["items"] if isinstance(item, dict)]
  print("\n== Feld-Inventar (Anzahl Artikel mit Wert / gesamt) ==")
  for path, count in field_presence(items, FIELDS_OF_INTEREST).items():
    marker = "OK " if count == len(items) else ("tw." if count else "-- ")
    print(f"  {marker} {path:<36} {count}/{len(items)}")

  if items:
    known = set(FIELDS_OF_INTEREST)
    extra = [key for key in dict.fromkeys(flatten_keys(items[0])) if key not in known]
    print("\n== Weitere Schlüssel im ersten Artikel (zur Info) ==")
    print("  " + ", ".join(extra))

  print("\n== Erste 5 Artikel (Plausibilitätsprüfung: passen sie zum Filter?) ==")
  for item in items[:5]:
    price = get_path(item, "price.amount")
    total = get_path(item, "total_item_price.amount")
    box = get_path(item, "item_box.first_line")
    ts = get_path(item, "photo.high_resolution.timestamp")
    age = ""
    if isinstance(ts, int | float):
      minutes = (datetime.now(UTC).timestamp() - ts) / 60
      age = f", Foto vor {minutes:.0f} min"
    print(
      f"  - {item.get('id')}: {item.get('title')!r} | {price} (gesamt {total}) | "
      f"Marke {item.get('brand_title') or box!r} | promoted={item.get('promoted')}{age}"
    )

  if items and items[0].get("id"):
    item_id = items[0]["id"]
    print(f"\n== Beispiel-Artikelseite: {endpoints.item_url(domain, item_id)}")


def save_fixture(catalog: dict[str, Any], domain: str) -> Path:
  FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
  path = FIXTURE_DIR / f"catalog_{domain}.json"
  path.write_text(
    json.dumps(anonymize_catalog(catalog), ensure_ascii=False, indent=2), encoding="utf-8"
  )
  return path


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="M0: einmalige Live-Probe der Vinted-Katalogsuche")
  parser.add_argument(
    "url", help="Vinted-Filter-Link aus der Adresszeile (vinted.at oder vinted.de)"
  )
  parser.add_argument("--ua", choices=["honest", "browser", "both"], default="both")
  parser.add_argument(
    "--browser-ua", default=DEFAULT_BROWSER_UA, help="User-Agent für die Browser-Variante"
  )
  parser.add_argument(
    "--dry-run", action="store_true", help="nur Link-Mapping zeigen, kein Netzwerk"
  )
  parser.add_argument("--no-fixture", action="store_true", help="keine Fixture speichern")
  args = parser.parse_args(argv)

  try:
    parsed = parse_filter_url(args.url)
  except InvalidFilterUrlError as error:
    print(f"Ungültiger Link: {error}", file=sys.stderr)
    return 2

  print("== Link-Mapping ==")
  print(f"  Domain:         vinted.{parsed.domain}")
  print(f"  Kanonische URL: {parsed.canonical_url}")
  print(f"  API-Endpunkt:   {endpoints.catalog_url(parsed.domain)}")
  for key, value in parsed.api_params.items():
    print(f"    {key} = {value}")
  if parsed.ignored:
    print(f"  IGNORIERT (unbekannt): {', '.join(parsed.ignored)}")
  if args.dry_run:
    return 0

  variants: list[tuple[str, str]] = []
  if args.ua in ("honest", "both"):
    variants.append(("ehrlich", HONEST_USER_AGENT))
  if args.ua in ("browser", "both"):
    variants.append(("browser", args.browser_ua))

  results: list[VariantResult] = []
  for index, (name, user_agent) in enumerate(variants):
    if index > 0:
      print(f"\n(Pause {PAUSE_BETWEEN_VARIANTS_S} s vor der nächsten Variante)")
      time.sleep(PAUSE_BETWEEN_VARIANTS_S)
    print(f"\n== Variante '{name}' – User-Agent: {user_agent}")
    results.append(run_variant(name, user_agent, parsed.domain, parsed.api_params))

  print("\n== Zusammenfassung ==")
  for result in results:
    print(f"  {result.name:<8} {'ERFOLG' if result.ok else 'FEHLGESCHLAGEN'}")

  successful = next((r for r in results if r.ok and r.catalog is not None), None)
  if successful is None:
    print("\nKeine Variante erfolgreich. Bitte NICHT wiederholt starten – Ausgabe an Claude geben.")
    return 1

  print_field_report(successful.catalog, parsed.domain)
  if not args.no_fixture:
    path = save_fixture(successful.catalog, parsed.domain)
    print(f"\nAnonymisierte Fixture gespeichert: {path}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
