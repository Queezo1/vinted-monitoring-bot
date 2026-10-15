from datetime import UTC, datetime
from decimal import Decimal

import discord

from tests.conftest import make_item
from vinted_bot.discord_bot.embeds import (
  build_message,
  description_preview,
  format_price,
  format_stars,
  gallery_urls,
  truncate,
)
from vinted_bot.storage.repository import OutboxEntry
from vinted_bot.vinted.models import SellerInfo

SEEN = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
PHOTOS = tuple(f"https://img.example.invalid/{n}.webp" for n in range(1, 6))


def entry(item_overrides: dict | None = None, seller: SellerInfo | None = None) -> OutboxEntry:
  return OutboxEntry(
    filter_id=1,
    item_id=7,
    channel_id=1,
    filter_name="rl-pullover",
    domain="at",
    item=make_item(7, **(item_overrides or {})),
    seller=seller,
    first_seen_at=SEEN,
  )


def fields(embed: discord.Embed) -> dict[str, str]:
  return {field.name or "": field.value or "" for field in embed.fields}


def test_format_price() -> None:
  assert format_price(Decimal("35"), "EUR") == "35,00 €"
  assert format_price(Decimal("1234.5"), "EUR") == "1.234,50 €"
  assert format_price(Decimal("9.99"), "PLN") == "9,99 PLN"
  assert format_price(None, "EUR") is None


def test_format_stars() -> None:
  assert format_stars(1.0, 14) == "★★★★★ (14)"
  assert format_stars(0.7, 3) == "★★★★☆ (3)"  # 3,5 -> 4
  assert format_stars(0.5, 0) is None  # keine Bewertungen
  assert format_stars(None, 5) is None


def test_truncate() -> None:
  assert truncate("abc", 3) == "abc"
  assert truncate("abcd", 3) == "ab…"


def test_description_preview() -> None:
  assert description_preview("Zeile 1\n\nZeile   2") == "> Zeile 1 Zeile 2"
  assert description_preview("x" * 300, limit=10) == "> " + "x" * 9 + "…"
  assert description_preview(None) is None
  assert description_preview("  \n ") is None


def test_gallery_urls() -> None:
  assert gallery_urls("main", PHOTOS) == list(PHOTOS[:3])
  assert gallery_urls("main", ()) == ["main"]
  assert gallery_urls(None, ()) == []
  assert gallery_urls(None, ("a", "a", "b")) == ["a", "b"]


async def test_full_message_with_seller_and_gallery() -> None:
  seller = SellerInfo(1007, "AT", 1.0, 14, "https://a/avatar.jpg")
  embeds, view = build_message(
    entry({"photo_urls": PHOTOS, "description": "Kaum getragen.\nNichtraucher."}, seller)
  )
  main = embeds[0]

  assert main.title == "🇦🇹 Artikel 7 – 35,00 €"
  assert main.url == "https://www.vinted.at/items/7-artikel"
  assert main.description == "> Kaum getragen. Nichtraucher."
  assert main.author.name == "seller_7"
  assert main.author.icon_url == "https://a/avatar.jpg"
  assert list(fields(main)) == [
    "⏱️ Gefunden",
    "📏 Größe",
    "🏷️ Marke",
    "✨ Zustand",
    "⭐ Bewertung",
    "💶 Preis",
  ]
  values = fields(main)
  assert values["⏱️ Gefunden"] == f"<t:{int(SEEN.timestamp())}:R>"
  assert values["⭐ Bewertung"] == "★★★★★ (14)"
  assert values["💶 Preis"] == "**35,00 €**\n37,45 € inkl. Käuferschutz"
  assert main.footer.text == "rl-pullover · vinted.at · ❤️ 3"

  # Galerie: 3 Embeds, alle mit derselben URL, je ein Bild
  assert len(embeds) == 3
  assert [e.image.url for e in embeds] == list(PHOTOS[:3])
  assert {e.url for e in embeds} == {main.url}

  buttons = [
    (b.label, str(b.emoji), b.url) for b in view.children if isinstance(b, discord.ui.Button)
  ]
  assert buttons == [
    ("Ansehen", "🔍", "https://www.vinted.at/items/7-artikel"),
    ("Verkäufer", "👤", "https://www.vinted.at/member/1007"),
  ]


async def test_missing_values_keep_the_grid() -> None:
  embeds, _ = build_message(
    entry(
      {
        "brand": None,
        "size": None,
        "photo_url": None,
        "seller_business": True,
        "favourite_count": 0,
      }
    )
  )
  main = embeds[0]
  assert len(embeds) == 1 and main.image.url is None
  assert main.title is not None and main.title.startswith("❔ ")  # Land unbekannt
  assert main.author.name == "seller_7 · Pro"
  values = fields(main)
  assert len(values) == 6  # Raster bleibt vollständig
  assert values["🏷️ Marke"] == "–" and values["📏 Größe"] == "–"
  assert values["⭐ Bewertung"] == "noch keine"
  assert main.description is None
  assert main.footer.text == "rl-pullover · vinted.at"


async def test_single_catalog_photo_without_details() -> None:
  embeds, _ = build_message(entry())
  assert len(embeds) == 1
  assert embeds[0].image.url == "https://images.example.invalid/1.jpg"


async def test_limits_are_respected() -> None:
  embeds, _ = build_message(
    entry({"title": "x" * 400, "description": "y" * 5000, "photo_urls": PHOTOS})
  )
  assert embeds[0].title is not None and len(embeds[0].title) == 256
  assert sum(len(e) for e in embeds) <= 6000
