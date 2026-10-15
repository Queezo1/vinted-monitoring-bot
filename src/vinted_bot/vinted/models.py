"""Vinted-JSON -> eigene, typisierte Datenklassen.

Defensiv geschrieben: Vinted ändert Felder ohne Vorwarnung (zuletzt 09/2026). Ein fehlendes
Feld kostet uns eine Zeile im Embed, nicht die ganze Benachrichtigung. Nur ohne `id` oder
`title` ist ein Artikel unbrauchbar und wird verworfen.

Feldherkunft siehe PLAN.md Abschnitt 0a (M0-Probe).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from vinted_bot.vinted.countries import normalize_country

# accessibility_label sieht so aus (de-AT):
#   "<Titel>, Marke: Ralph Lauren, Zustand: Sehr gut, Größe: M, 35.00 €, 37.45 €"
# Der Titel kann selbst Kommas enthalten, darum suchen wir gezielt nach ", Schlüssel: Wert".
_LABEL_CONDITION = re.compile(r", Zustand: ([^,]+)")
_LABEL_SIZE = re.compile(r", Größe: ([^,]+)")
_SECOND_LINE_SEPARATOR = " · "


@dataclass(frozen=True)
class VintedItem:
  id: int
  title: str
  url: str  # absolut
  price: Decimal | None
  total_price: Decimal | None  # inkl. Käuferschutz
  currency: str | None
  brand: str | None
  size: str | None
  condition: str | None
  photo_url: str | None
  seller_id: int | None
  seller_login: str | None
  seller_business: bool
  promoted: bool
  favourite_count: int | None
  # Nur über die Artikelseite verfügbar (vinted/item_page.py), daher mit Standardwerten
  photo_urls: tuple[str, ...] = ()
  description: str | None = None

  def to_json_dict(self) -> dict[str, Any]:
    """Für die Outbox in SQLite (Decimal ist nicht JSON-fähig -> str)."""
    data = asdict(self)
    for key in ("price", "total_price"):
      if data[key] is not None:
        data[key] = str(data[key])
    return data

  @classmethod
  def from_json_dict(cls, data: dict[str, Any]) -> VintedItem:
    data = dict(data)
    for key in ("price", "total_price"):
      if data.get(key) is not None:
        data[key] = Decimal(data[key])
    # JSON kennt keine Tupel -> Liste wieder in Tupel umwandeln
    data["photo_urls"] = tuple(data.get("photo_urls") or ())
    return cls(**data)


@dataclass(frozen=True)
class SellerInfo:
  user_id: int
  country_iso: str | None
  feedback_reputation: float | None  # 0..1
  feedback_count: int | None
  photo_url: str | None


def parse_item(raw: dict[str, Any], domain: str) -> VintedItem | None:
  """Ein Artikel aus der Katalog-Antwort. None, wenn id oder title fehlen."""
  item_id = _as_int(raw.get("id"))
  title = _as_text(raw.get("title"))
  if item_id is None or title is None:
    return None

  price, currency = _money(raw.get("price"))
  total_price, total_currency = _money(raw.get("total_item_price"))

  box = raw.get("item_box") if isinstance(raw.get("item_box"), dict) else {}
  label = _as_text(box.get("accessibility_label")) or ""
  size, condition = _size_and_condition(label, _as_text(box.get("second_line")))

  photo = raw.get("photo") if isinstance(raw.get("photo"), dict) else {}
  user = raw.get("user") if isinstance(raw.get("user"), dict) else {}

  # url ist seit 09/2026 relativ ("/items/123-slug"); ältere Antworten waren absolut
  url = _as_text(raw.get("url")) or f"/items/{item_id}"
  if url.startswith("/"):
    url = f"https://www.vinted.{domain}{url}"

  return VintedItem(
    id=item_id,
    title=title,
    url=url,
    price=price,
    total_price=total_price,
    currency=currency or total_currency,
    # Fallback auf das alte Feld, falls Vinted es wieder einführt
    brand=_as_text(box.get("first_line")) or _as_text(raw.get("brand_title")),
    size=size or _as_text(raw.get("size_title")),
    condition=condition or _as_text(raw.get("status")),
    photo_url=_as_text(photo.get("full_size_url")) or _as_text(photo.get("url")),
    seller_id=_as_int(user.get("id")),
    seller_login=_as_text(user.get("login")),
    seller_business=bool(user.get("business")),
    promoted=bool(raw.get("promoted")),
    favourite_count=_as_int(raw.get("favourite_count")),
  )


def parse_seller(raw: dict[str, Any]) -> SellerInfo | None:
  """Antwort von /api/v2/users/{id}. Liest bewusst NUR die Felder, die das Embed braucht."""
  user = raw.get("user") if isinstance(raw.get("user"), dict) else raw
  user_id = _as_int(user.get("id"))
  if user_id is None:
    return None
  photo = user.get("photo") if isinstance(user.get("photo"), dict) else {}
  thumbs = photo.get("thumbnails") if isinstance(photo.get("thumbnails"), list) else []
  # kleines Vorschaubild reicht für die Autor-Zeile
  thumb_url = next((t.get("url") for t in thumbs if isinstance(t, dict) and t.get("url")), None)
  reputation = user.get("feedback_reputation")
  return SellerInfo(
    user_id=user_id,
    country_iso=normalize_country(_as_text(user.get("country_iso_code"))),
    feedback_reputation=float(reputation) if isinstance(reputation, int | float) else None,
    feedback_count=_as_int(user.get("feedback_count")),
    photo_url=_as_text(thumb_url) or _as_text(photo.get("url")),
  )


def _size_and_condition(label: str, second_line: str | None) -> tuple[str | None, str | None]:
  size = _first_group(_LABEL_SIZE, label)
  condition = _first_group(_LABEL_CONDITION, label)
  if (size is None or condition is None) and second_line:
    # second_line: "M · Sehr gut" oder nur "Sehr gut" (ohne Größe)
    parts = [p.strip() for p in second_line.split(_SECOND_LINE_SEPARATOR) if p.strip()]
    if len(parts) >= 2:
      size = size or parts[0]
      condition = condition or parts[-1]
    elif len(parts) == 1:
      condition = condition or parts[0]
  return size, condition


def _first_group(pattern: re.Pattern[str], text: str) -> str | None:
  match = pattern.search(text)
  return match.group(1).strip() if match else None


def _money(value: Any) -> tuple[Decimal | None, str | None]:
  if not isinstance(value, dict):
    return None, None
  try:
    amount = Decimal(str(value.get("amount")))
  except InvalidOperation:
    amount = None
  return amount, _as_text(value.get("currency_code"))


def _as_text(value: Any) -> str | None:
  if not isinstance(value, str):
    return None
  value = value.strip()
  return value or None


def _as_int(value: Any) -> int | None:
  if isinstance(value, bool):  # bool ist in Python eine Unterklasse von int
    return None
  if isinstance(value, int):
    return value
  if isinstance(value, str) and value.isdigit():
    return int(value)
  return None
