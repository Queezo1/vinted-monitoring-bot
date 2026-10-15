"""Gemeinsame Test-Helfer."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from vinted_bot.storage.db import open_database
from vinted_bot.storage.repository import Repository
from vinted_bot.vinted.models import VintedItem

FIXTURES = Path(__file__).parent / "fixtures"


def load_catalog_fixture() -> dict[str, Any]:
  """Echte, anonymisierte Vinted-Antwort aus der M0-Probe (15.10.2026)."""
  return json.loads((FIXTURES / "catalog_at.json").read_text(encoding="utf-8"))


class FakeClock:
  """Steuerbare Uhr für Repository-Tests (statt echter Zeit)."""

  def __init__(self, start: datetime | None = None) -> None:
    self.now = start or datetime(2026, 10, 15, 12, 0, tzinfo=UTC)

  def __call__(self) -> datetime:
    return self.now


@pytest.fixture
def clock() -> FakeClock:
  return FakeClock()


@pytest.fixture
async def repo(clock: FakeClock) -> AsyncIterator[Repository]:
  conn = await open_database(":memory:")
  yield Repository(conn, now=clock)
  await conn.close()


def make_item(item_id: int, **overrides: Any) -> VintedItem:
  values: dict[str, Any] = {
    "id": item_id,
    "title": f"Artikel {item_id}",
    "url": f"https://www.vinted.at/items/{item_id}-artikel",
    "price": Decimal("35.00"),
    "total_price": Decimal("37.45"),
    "currency": "EUR",
    "brand": "Ralph Lauren",
    "size": "M",
    "condition": "Sehr gut",
    "photo_url": "https://images.example.invalid/1.jpg",
    "seller_id": 1000 + item_id,
    "seller_login": f"seller_{item_id}",
    "seller_business": False,
    "promoted": False,
    "favourite_count": 3,
  }
  values.update(overrides)
  return VintedItem(**values)


def raw_item(item_id: int, *, seller_id: int = 1, promoted: bool = False) -> dict[str, Any]:
  """Minimaler Artikel im Format der svc-catalogue-Antwort."""
  return {
    "id": item_id,
    "title": f"Pullover {item_id}",
    "url": f"/items/{item_id}-pullover",
    "price": {"amount": "30.00", "currency_code": "EUR"},
    "total_item_price": {"amount": "32.20", "currency_code": "EUR"},
    "item_box": {
      "first_line": "Ralph Lauren",
      "second_line": "L · Sehr gut",
      "accessibility_label": f"Pullover {item_id}, Marke: Ralph Lauren, Zustand: Sehr gut, "
      "Größe: L, 30.00 €, 32.20 €",
    },
    "photo": {"url": "https://images.example.invalid/x.jpg"},
    "user": {"id": seller_id, "login": f"seller_{seller_id}", "business": False},
    "promoted": promoted,
  }
