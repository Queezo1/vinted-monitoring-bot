"""Outbox-Eintrag -> Discord-Embeds + Link-Buttons. Reine Funktionen, ohne Netzwerk.

Layout (Vorbild Vintrez, optimiert für Desktop/Browser):
  Autor-Zeile     Verkäufer (+ "Pro"), Avatar, Link zum Profil
  Titel           Flagge + Titel + Preis, Link zum Artikel
  Beschreibung    Kurzbeschreibung als Zitat
  Felder (3 × 2)  Gefunden · Größe · Marke / Zustand · Bewertung · Preis
  Galerie         bis zu 3 Fotos
  Footer          Filtername · Domain · Likes, dazu der Zeitpunkt

Galerie-Trick (nicht offiziell dokumentiert, aber weit verbreitet): Mehrere Embeds mit
DERSELBEN `url` zeigt Discord als ein Embed mit Bildraster an. Ändert Discord das, sieht
man stattdessen untereinander gestapelte Bilder – unschön, aber nichts geht kaputt.
"""

from __future__ import annotations

import re
from decimal import Decimal

import discord

from vinted_bot.storage.repository import OutboxEntry
from vinted_bot.vinted import endpoints
from vinted_bot.vinted.countries import UNKNOWN_FLAG, flag_emoji

VINTED_TEAL = 0x09B1BA
MAX_GALLERY_PHOTOS = 3
DESCRIPTION_PREVIEW = 200  # Zeichen der Artikelbeschreibung im Embed
EMPTY = "–"

# Offizielle Discord-Limits (docs.discord.com, Embed Limits)
TITLE_LIMIT = 256
AUTHOR_LIMIT = 256
FIELD_VALUE_LIMIT = 1024

_CURRENCY_SYMBOLS = {"EUR": "€", "GBP": "£", "USD": "$"}


def truncate(text: str, limit: int) -> str:
  """Kürzt auf `limit` Zeichen und hängt dann "…" an (zählt mit)."""
  return text if len(text) <= limit else text[: limit - 1] + "…"


def format_price(amount: Decimal | None, currency: str | None) -> str | None:
  """Decimal('1234.5') + 'EUR' -> '1.234,50 €' (österreichisches Format)."""
  if amount is None:
    return None
  # Erst englisch formatieren ("1,234.50"), dann Trennzeichen tauschen
  text = f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
  symbol = _CURRENCY_SYMBOLS.get(currency or "", currency or "")
  return f"{text} {symbol}".strip()


def format_stars(reputation: float | None, count: int | None) -> str | None:
  """0.92, 128 -> '★★★★★ (128)'. Vinted liefert die Bewertung als Anteil 0..1."""
  if reputation is None or not count:
    return None
  stars = max(0, min(5, round(reputation * 5)))
  return f"{'★' * stars}{'☆' * (5 - stars)} ({count})"


def description_preview(text: str | None, limit: int = DESCRIPTION_PREVIEW) -> str | None:
  """Erste Zeichen der Beschreibung, Zeilenumbrüche zu Leerzeichen, als Discord-Zitat."""
  if not text:
    return None
  flat = re.sub(r"\s+", " ", text).strip()
  return f"> {truncate(flat, limit)}" if flat else None


def gallery_urls(primary: str | None, all_photos: tuple[str, ...]) -> list[str]:
  """Bis zu 3 eindeutige Foto-URLs, Hauptfoto zuerst."""
  urls = list(all_photos) or ([primary] if primary else [])
  return list(dict.fromkeys(urls))[:MAX_GALLERY_PHOTOS]


def build_message(entry: OutboxEntry) -> tuple[list[discord.Embed], discord.ui.View]:
  item, seller = entry.item, entry.seller
  flag = flag_emoji(seller.country_iso) if seller else UNKNOWN_FLAG
  price = format_price(item.price, item.currency)
  total = format_price(item.total_price, item.currency)

  title = f"{flag} {item.title}" + (f" – {price}" if price else "")
  main = discord.Embed(
    title=truncate(title, TITLE_LIMIT),
    url=item.url,
    description=description_preview(item.description),
    color=VINTED_TEAL,
    timestamp=entry.first_seen_at,  # Discord zeigt das in deiner lokalen Zeit
  )

  if item.seller_login:
    author = item.seller_login + (" · Pro" if item.seller_business else "")
    main.set_author(
      name=truncate(author, AUTHOR_LIMIT),
      url=endpoints.member_url(entry.domain, item.seller_id) if item.seller_id else None,
      icon_url=seller.photo_url if seller and seller.photo_url else None,
    )

  # Immer alle 6 Felder, damit das 3×2-Raster bei jedem Artikel gleich aussieht.
  # <t:...:R> rendert Discord als "vor 2 Minuten" und aktualisiert es selbst.
  rating = format_stars(seller.feedback_reputation, seller.feedback_count) if seller else None
  price_text = f"**{price}**" if price else None
  if price_text and total and total != price:
    price_text += f"\n{total} inkl. Käuferschutz"
  _add_field(main, "⏱️ Gefunden", f"<t:{int(entry.first_seen_at.timestamp())}:R>")
  _add_field(main, "📏 Größe", item.size)
  _add_field(main, "🏷️ Marke", item.brand)
  _add_field(main, "✨ Zustand", item.condition)
  _add_field(main, "⭐ Bewertung", rating or "noch keine")
  _add_field(main, "💶 Preis", price_text)

  footer = f"{entry.filter_name} · vinted.{entry.domain}"
  if item.favourite_count:
    footer += f" · ❤️ {item.favourite_count}"
  main.set_footer(text=footer)

  embeds = [main]
  photos = gallery_urls(item.photo_url, item.photo_urls)
  if photos:
    main.set_image(url=photos[0])
    for photo in photos[1:]:
      # Gleiche url wie das Haupt-Embed -> Discord fasst die Bilder zur Galerie zusammen
      embeds.append(discord.Embed(url=item.url, color=VINTED_TEAL).set_image(url=photo))

  view = discord.ui.View(timeout=None)
  view.add_item(
    discord.ui.Button(style=discord.ButtonStyle.link, label="Ansehen", emoji="🔍", url=item.url)
  )
  if item.seller_id:
    view.add_item(
      discord.ui.Button(
        style=discord.ButtonStyle.link,
        label="Verkäufer",
        emoji="👤",
        url=endpoints.member_url(entry.domain, item.seller_id),
      )
    )
  return embeds, view


def _add_field(embed: discord.Embed, name: str, value: str | None) -> None:
  embed.add_field(name=name, value=truncate(value or EMPTY, FIELD_VALUE_LIMIT), inline=True)
