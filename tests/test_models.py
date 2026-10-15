from decimal import Decimal

from tests.conftest import load_catalog_fixture, make_item
from vinted_bot.vinted.models import VintedItem, parse_item, parse_seller


def test_every_item_of_the_real_response_is_parsed() -> None:
  raw_items = load_catalog_fixture()["items"]
  items = [parse_item(raw, "at") for raw in raw_items]
  assert all(item is not None for item in items)
  assert len(items) == 20


def test_fields_from_real_response() -> None:
  item = parse_item(load_catalog_fixture()["items"][0], "at")
  assert item is not None
  assert item.id == 10383286812
  assert item.url.startswith("https://www.vinted.at/items/10383286812-")
  assert item.price == Decimal("35.00")
  assert item.total_price == Decimal("37.45")
  assert item.currency == "EUR"
  assert item.brand == "Ralph Lauren"
  assert item.size == "M"
  assert item.condition == "Sehr gut"
  assert item.photo_url and item.photo_url.startswith("https://")
  assert item.seller_id is not None and item.seller_login
  assert item.promoted is False


def test_label_with_commas_in_title() -> None:
  raw = {
    "id": 1,
    "title": "Jacke, warm, blau",
    "item_box": {
      "accessibility_label": "Jacke, warm, blau, Marke: Nike, Zustand: Neu, Größe: XL, 5.00 €",
    },
  }
  item = parse_item(raw, "de")
  assert item is not None
  assert (item.size, item.condition) == ("XL", "Neu")
  assert item.url == "https://www.vinted.de/items/1"  # Fallback ohne url-Feld


def test_second_line_fallback_without_size() -> None:
  raw = {"id": 1, "title": "Tasche", "item_box": {"second_line": "Gut"}}
  item = parse_item(raw, "at")
  assert item is not None
  assert item.size is None
  assert item.condition == "Gut"


def test_second_line_fallback_with_size() -> None:
  raw = {"id": 1, "title": "Hose", "item_box": {"second_line": "W32 · Neu mit Etikett"}}
  item = parse_item(raw, "at")
  assert item is not None
  assert (item.size, item.condition) == ("W32", "Neu mit Etikett")


def test_old_field_names_still_work_as_fallback() -> None:
  raw = {"id": 1, "title": "Alt", "brand_title": "Adidas", "size_title": "S", "status": "Gut"}
  item = parse_item(raw, "at")
  assert item is not None
  assert (item.brand, item.size, item.condition) == ("Adidas", "S", "Gut")


def test_unusable_items_are_rejected() -> None:
  assert parse_item({"title": "ohne id"}, "at") is None
  assert parse_item({"id": 1}, "at") is None
  assert parse_item({"id": True, "title": "bool ist keine id"}, "at") is None


def test_broken_price_does_not_crash() -> None:
  item = parse_item({"id": 1, "title": "x", "price": {"amount": "abc"}}, "at")
  assert item is not None and item.price is None


def test_json_roundtrip() -> None:
  item = make_item(5, photo_urls=("https://a/1", "https://a/2"), description="Text")
  assert VintedItem.from_json_dict(item.to_json_dict()) == item


def test_old_outbox_payload_without_new_fields_still_loads() -> None:
  data = make_item(5).to_json_dict()
  del data["photo_urls"], data["description"]
  assert VintedItem.from_json_dict(data).photo_urls == ()


def test_parse_seller_reads_only_needed_fields() -> None:
  raw = {
    "user": {
      "id": 42,
      "login": "x",
      "real_name": "Geheim",
      "city": "Wien",
      "country_iso_code": "de",
      "feedback_reputation": 0.9,
      "feedback_count": 14,
      "photo": {"url": "https://a/big.jpg", "thumbnails": [{"url": "https://a/small.jpg"}]},
    }
  }
  seller = parse_seller(raw)
  assert seller is not None
  assert seller.country_iso == "DE"
  assert seller.feedback_reputation == 0.9
  assert seller.feedback_count == 14
  assert seller.photo_url == "https://a/small.jpg"
  assert "Geheim" not in repr(seller) and "Wien" not in repr(seller)


def test_parse_seller_without_id() -> None:
  assert parse_seller({"user": {"login": "x"}}) is None
