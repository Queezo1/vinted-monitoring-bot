"""Parser für die Artikelseite – gegen einen Ausschnitt der echten Seite (15.10.2026)."""

from tests.conftest import FIXTURES
from vinted_bot.vinted.item_page import parse_item_page

PAGE = (FIXTURES / "item_page_at.html").read_text(encoding="utf-8")
ITEM_ID = 10383526942
TITLE = "Polo Ralph Lauren Pullover Schwarz V-Ausschnitt Herren Strick"


def test_all_photos_of_the_item_in_order() -> None:
  details = parse_item_page(PAGE, ITEM_ID, TITLE)
  assert len(details.photo_urls) == 5
  assert all(url.startswith("https://") and "/f800/" in url for url in details.photo_urls)
  assert "06_02509" in details.photo_urls[0]  # Hauptfoto zuerst


def test_photos_of_other_items_are_ignored() -> None:
  # Die Fixture enthält VOR unserem Artikel ein fremdes photos-Array (item_id 999)
  details = parse_item_page(PAGE, ITEM_ID, TITLE)
  assert not any("fremd" in url for url in details.photo_urls)


def test_description_without_title_prefix() -> None:
  details = parse_item_page(PAGE, ITEM_ID, TITLE)
  assert details.description is not None
  assert details.description.startswith("Polo Ralph Lauren Pullover in Schwarz mit V-Ausschnitt")
  assert "&amp;" not in details.description  # HTML-Entities aufgelöst


def test_unknown_item_or_garbage_gives_empty_result() -> None:
  assert parse_item_page(PAGE, 123, TITLE).photo_urls == ()
  empty = parse_item_page("<html>Challenge</html>", ITEM_ID, TITLE)
  assert empty.photo_urls == () and empty.description is None


def test_unescaped_json_variant() -> None:
  page = '{"item_id":"5","photos":[{"url":"https://a/1.webp"},{"url":"https://a/2.webp"}]}'
  assert parse_item_page(page, 5, "x").photo_urls == ("https://a/1.webp", "https://a/2.webp")


def test_broken_array_does_not_crash() -> None:
  page = '{"item_id":"5","photos":[{"url": kaputt'
  assert parse_item_page(page, 5, "x").photo_urls == ()
