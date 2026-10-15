from vinted_bot.vinted import endpoints


def test_urls_per_domain() -> None:
  assert endpoints.site_root("at") == "https://www.vinted.at/"
  assert endpoints.catalog_url("de") == "https://api.vinted.de/svc-catalogue/items"
  assert endpoints.user_url("at", 7) == "https://www.vinted.at/api/v2/users/7"
  assert endpoints.item_url("at", 42) == "https://www.vinted.at/items/42"
  assert endpoints.member_url("at", 7) == "https://www.vinted.at/member/7"
