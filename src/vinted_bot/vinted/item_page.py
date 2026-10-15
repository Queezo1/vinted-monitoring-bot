"""Artikelseite (HTML) -> alle Fotos + Beschreibung.

Die Katalog-Suche liefert nur 1 Foto und keine Beschreibung. Der JSON-Detail-Endpunkt ist
seit Langem mit 403 gesperrt. Die öffentliche Artikelseite enthält beides aber:
  - Fotos: im eingebetteten Next.js-Datenblock als `\\"photos\\":[{...}]`, direkt nach der
    `item_id` des Artikels. Weiter unten stehen auch Fotos ÄHNLICHER Artikel – deshalb prüfen
    wir die item_id vor dem Array.
  - Beschreibung: im Meta-Tag `og:description` als "<Titel> - <Beschreibung>".
Stand: 15.10.2026. Wenn Vinted das ändert, liefert die Funktion einfach leere Werte und der
Post erscheint mit nur einem Foto – kein Absturz.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass

from vinted_bot.vinted.models import https_url

# Im HTML steht der Datenblock als JS-String, Anführungszeichen sind daher als \" escaped
_PHOTOS_KEYS = ('\\"photos\\":[', '"photos":[')
_ITEM_ID_LOOKBEHIND = 400  # so weit vor dem Array muss die item_id stehen
_OG_DESCRIPTION = re.compile(r'<meta\s+property="og:description"\s+content="([^"]*)"')


@dataclass(frozen=True)
class ItemDetails:
  photo_urls: tuple[str, ...]
  description: str | None


def parse_item_page(page: str, item_id: int, title: str) -> ItemDetails:
  return ItemDetails(photo_urls=_photo_urls(page, item_id), description=_description(page, title))


def _photo_urls(page: str, item_id: int) -> tuple[str, ...]:
  for key in _PHOTOS_KEYS:
    escaped = key.startswith("\\")
    id_marker = f'item_id\\":\\"{item_id}\\"' if escaped else f'item_id":"{item_id}"'
    start = 0
    while (index := page.find(key, start)) != -1:
      start = index + len(key)
      # Gehört dieses Array zu unserem Artikel?
      if id_marker not in page[max(0, index - _ITEM_ID_LOOKBEHIND) : index]:
        continue
      photos = _parse_json_array(page, index + len(key) - 1, escaped)
      if photos is None:
        continue
      urls = [_best_url(photo) for photo in photos if isinstance(photo, dict)]
      return tuple(url for url in urls if url)
  return ()


def _parse_json_array(page: str, open_bracket: int, escaped: bool) -> list | None:
  """Liest ein JSON-Array ab `open_bracket` durch Klammerzählen und parst es."""
  depth = 0
  for position in range(open_bracket, len(page)):
    char = page[position]
    if char == "[":
      depth += 1
    elif char == "]":
      depth -= 1
      if depth == 0:
        raw = page[open_bracket : position + 1]
        try:
          if escaped:
            # Erst die JS-String-Escapes auflösen (\" -> "), dann das eigentliche JSON parsen
            raw = json.loads(f'"{raw}"')
          result = json.loads(raw)
        except ValueError:
          return None
        return result if isinstance(result, list) else None
  return None


def _best_url(photo: dict) -> str | None:
  url = https_url(photo.get("full_size_url")) or https_url(photo.get("url"))
  if url:
    return url
  thumbnails = photo.get("thumbnails")
  if isinstance(thumbnails, list) and thumbnails and isinstance(thumbnails[-1], dict):
    return https_url(thumbnails[-1].get("url"))
  return None


def _description(page: str, title: str) -> str | None:
  match = _OG_DESCRIPTION.search(page)
  if not match:
    return None
  text = html_lib.unescape(match.group(1)).strip()
  # Format: "<Titel> - <Beschreibung>" -> Titel abschneiden
  prefix = f"{title} - "
  if text.startswith(prefix):
    text = text[len(prefix) :]
  elif text == title:
    return None
  text = re.sub(r"\n{3,}", "\n\n", text).strip()
  return text or None
