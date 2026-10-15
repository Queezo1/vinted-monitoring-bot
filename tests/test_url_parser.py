import pytest

from vinted_bot.vinted.url_parser import InvalidFilterUrlError, parse_filter_url


def test_typical_at_link_is_mapped_to_new_attribute_names() -> None:
  parsed = parse_filter_url(
    "https://www.vinted.at/catalog?search_text=nike%20air&catalog[]=1206&brand_ids[]=53"
    "&size_ids[]=208&size_ids[]=207&status_ids[]=6&price_from=10&price_to=50&currency=EUR"
    "&order=relevance&time=1760000000&search_id=999&page=3&disabled_personalization=true"
  )
  assert parsed.domain == "at"
  assert parsed.api_params == {
    "search_text": "nike air",
    "price_from": "10",
    "price_to": "50",
    "currency": "EUR",
    "attribute_ids[catalog]": "1206",
    "attribute_ids[brand]": "53",
    "attribute_ids[size]": "207,208",  # sortiert
    "attribute_ids[status]": "6",
    "order": "newest_first",  # immer erzwungen
  }
  assert parsed.ignored == ()


def test_old_parameter_names_never_reach_the_api() -> None:
  # Die API ignoriert alte Namen stillschweigend -> sie dürfen nie durchgereicht werden
  parsed = parse_filter_url(
    "https://www.vinted.de/catalog?brand_ids[]=53&catalog_ids=5&status_ids=1"
  )
  assert not any(key in parsed.api_params for key in ("brand_ids", "catalog_ids", "status_ids"))
  assert parsed.api_params["attribute_ids[brand]"] == "53"
  assert parsed.api_params["attribute_ids[catalog]"] == "5"


@pytest.mark.parametrize(
  ("url", "key", "expected"),
  [
    ("https://www.vinted.at/catalog/1206-herren-schuhe", "attribute_ids[catalog]", "1206"),
    ("https://www.vinted.at/brand/53-nike", "attribute_ids[brand]", "53"),
    ("https://www.vinted.at/catalog?color_ids[]=1&color_ids[]=12", "attribute_ids[color]", "1,12"),
    ("https://www.vinted.at/catalog?material_ids[]=44", "attribute_ids[material]", "44"),
    ("https://www.vinted.at/catalog?country_ids[]=15&search_text=x", "country_ids", "15"),
    ("https://www.vinted.at/catalog?brand_ids=53,14", "attribute_ids[brand]", "14,53"),
    ("https://www.vinted.at/catalog?catalog%5B%5D=77", "attribute_ids[catalog]", "77"),
  ],
)
def test_parameter_variants(url: str, key: str, expected: str) -> None:
  assert parse_filter_url(url).api_params[key] == expected


def test_plus_in_search_text_means_space() -> None:
  assert (
    parse_filter_url("vinted.at/catalog?search_text=carhartt+jacke").api_params["search_text"]
    == "carhartt jacke"
  )


def test_umlauts_survive() -> None:
  parsed = parse_filter_url("https://www.vinted.at/catalog?search_text=m%C3%BCtze")
  assert parsed.api_params["search_text"] == "mütze"


def test_empty_values_are_dropped() -> None:
  # Leere Werte führen bei svc-catalogue zu HTTP 400
  parsed = parse_filter_url("https://www.vinted.at/catalog?search_text=nike&price_from=&price_to=")
  assert "price_from" not in parsed.api_params
  assert "price_to" not in parsed.api_params


def test_decimal_comma_in_price_is_normalised() -> None:
  assert (
    parse_filter_url("vinted.at/catalog?search_text=a&price_to=9,99").api_params["price_to"]
    == "9.99"
  )


def test_unknown_and_invalid_values_are_reported() -> None:
  parsed = parse_filter_url(
    "https://www.vinted.at/catalog?search_text=a&video_game_rating_ids[]=3&brand_ids[]=abc&price_from=zehn"
  )
  assert set(parsed.ignored) == {"video_game_rating_ids=3", "brand_ids=abc", "price_from=zehn"}


def test_canonical_url_is_stable_regardless_of_order_and_tracking() -> None:
  a = parse_filter_url("https://www.vinted.at/catalog?brand_ids[]=53&search_text=nike&time=1")
  b = parse_filter_url("vinted.at/catalog?search_text=nike&brand_ids[]=53&search_id=2&page=4")
  assert a.canonical_url == b.canonical_url
  assert a.canonical_url.startswith("https://www.vinted.at/catalog?")


def test_duplicate_ids_are_removed() -> None:
  parsed = parse_filter_url("vinted.at/catalog?size_ids[]=5&size_ids[]=5&size_ids[]=3")
  assert parsed.api_params["attribute_ids[size]"] == "3,5"


@pytest.mark.parametrize(
  "url",
  [
    "",
    "   ",
    "https://www.ebay.at/sch?q=nike",
    "https://www.vinted.fr/catalog?search_text=nike",  # nicht unterstützt
    "https://vinted.at.evil.example/catalog?search_text=nike",
    "https://www.vinted.at/catalog",  # keine Filter
    "https://www.vinted.at/catalog?order=newest_first&time=1&currency=EUR",  # nur Rauschen
  ],
)
def test_invalid_links_are_rejected(url: str) -> None:
  with pytest.raises(InvalidFilterUrlError):
    parse_filter_url(url)
