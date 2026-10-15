"""VintedClient gegen gemockte HTTP-Antworten (respx) – kein echtes Netzwerk."""

from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from tests.conftest import load_catalog_fixture
from vinted_bot.vinted.client import VintedClient
from vinted_bot.vinted.errors import (
  BlockedError,
  MalformedResponseError,
  RateLimitedError,
  UnexpectedStatusError,
  VintedNetworkError,
)
from vinted_bot.vinted.rate_limiter import GlobalRateLimiter

HOME = "https://www.vinted.at/"
CATALOG = "https://api.vinted.at/svc-catalogue/items"
USER = "https://www.vinted.at/api/v2/users/42"
UA = "TestAgent/1.0"


def home_ok(token: str = "tok1") -> httpx.Response:
  return httpx.Response(
    200,
    headers={"set-cookie": f"access_token_web={token}; Path=/", "X-Anon-Id": "anon-1"},
    text="<html></html>",
  )


class CountingLimiter(GlobalRateLimiter):
  def __init__(self) -> None:
    super().__init__(0)
    self.calls = 0
    self.low_priority_calls = 0

  async def acquire(self, *, low_priority: bool = False) -> None:
    self.calls += 1
    self.low_priority_calls += int(low_priority)


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
  async with httpx.AsyncClient() as client:
    yield client


@pytest.fixture
def limiter() -> CountingLimiter:
  return CountingLimiter()


@pytest.fixture
def client(http: httpx.AsyncClient, limiter: CountingLimiter) -> VintedClient:
  return VintedClient(http, limiter, UA)


@respx.mock
async def test_catalog_happy_path(client: VintedClient, limiter: CountingLimiter) -> None:
  respx.get(HOME).mock(return_value=home_ok())
  route = respx.get(CATALOG).mock(return_value=httpx.Response(200, json=load_catalog_fixture()))

  items = await client.fetch_catalog("at", {"attribute_ids[brand]": "88"})

  assert len(items) == 20
  request = route.calls.last.request
  assert request.headers["Cookie"] == "access_token_web=tok1"
  assert request.headers["X-Anon-Id"] == "anon-1"
  assert request.headers["User-Agent"] == UA
  assert request.url.params["order"] == "newest_first"
  assert request.url.params["attribute_ids[brand]"] == "88"
  assert request.url.params["per_page"] == "20"
  assert limiter.calls == 2  # Startseite + Katalog, beide durch den Limiter


@respx.mock
async def test_session_is_reused(client: VintedClient, limiter: CountingLimiter) -> None:
  home = respx.get(HOME).mock(return_value=home_ok())
  respx.get(CATALOG).mock(return_value=httpx.Response(200, json={"items": []}))
  await client.fetch_catalog("at", {})
  await client.fetch_catalog("at", {})
  assert home.call_count == 1
  assert limiter.calls == 3


@respx.mock
async def test_401_refreshes_session_exactly_once(client: VintedClient) -> None:
  home = respx.get(HOME).mock(side_effect=[home_ok("old"), home_ok("new")])
  catalog = respx.get(CATALOG).mock(
    side_effect=[httpx.Response(401), httpx.Response(200, json={"items": [{"id": 1}]})]
  )
  items = await client.fetch_catalog("at", {})
  assert items == [{"id": 1}]
  assert home.call_count == 2
  assert catalog.calls.last.request.headers["Cookie"] == "access_token_web=new"


@respx.mock
async def test_second_401_is_not_retried_again(client: VintedClient) -> None:
  respx.get(HOME).mock(return_value=home_ok())
  catalog = respx.get(CATALOG).mock(return_value=httpx.Response(401))
  with pytest.raises(Exception):  # noqa: B017 – genaue Klasse egal, nur kein Endlos-Retry
    await client.fetch_catalog("at", {})
  assert catalog.call_count == 2


@respx.mock
async def test_403_raises_blocked_without_retry(client: VintedClient) -> None:
  respx.get(HOME).mock(return_value=home_ok())
  catalog = respx.get(CATALOG).mock(return_value=httpx.Response(403, text="blocked"))
  with pytest.raises(BlockedError):
    await client.fetch_catalog("at", {})
  assert catalog.call_count == 1


@respx.mock
async def test_home_without_token_counts_as_blocked(client: VintedClient) -> None:
  respx.get(HOME).mock(return_value=httpx.Response(200, text="<html>captcha</html>"))
  with pytest.raises(BlockedError):
    await client.fetch_catalog("at", {})


@respx.mock
async def test_429_carries_retry_after(client: VintedClient) -> None:
  respx.get(HOME).mock(return_value=home_ok())
  respx.get(CATALOG).mock(return_value=httpx.Response(429, headers={"Retry-After": "120"}))
  with pytest.raises(RateLimitedError) as info:
    await client.fetch_catalog("at", {})
  assert info.value.retry_after_s == 120


@respx.mock
async def test_html_instead_of_json_is_malformed(client: VintedClient) -> None:
  respx.get(HOME).mock(return_value=home_ok())
  respx.get(CATALOG).mock(return_value=httpx.Response(200, text="<!DOCTYPE html><html>"))
  with pytest.raises(MalformedResponseError):
    await client.fetch_catalog("at", {})


@respx.mock
async def test_json_without_items_is_malformed(client: VintedClient) -> None:
  respx.get(HOME).mock(return_value=home_ok())
  respx.get(CATALOG).mock(return_value=httpx.Response(200, json={"code": 0}))
  with pytest.raises(MalformedResponseError):
    await client.fetch_catalog("at", {})


@respx.mock
async def test_other_status_codes(client: VintedClient) -> None:
  respx.get(HOME).mock(return_value=home_ok())
  respx.get(CATALOG).mock(return_value=httpx.Response(400, json={"code": "INVALID_REQUEST"}))
  with pytest.raises(UnexpectedStatusError) as info:
    await client.fetch_catalog("at", {})
  assert info.value.status_code == 400


@respx.mock
async def test_network_error(client: VintedClient) -> None:
  respx.get(HOME).mock(side_effect=httpx.ConnectTimeout("timeout"))
  with pytest.raises(VintedNetworkError):
    await client.fetch_catalog("at", {})


@respx.mock
async def test_fetch_user(client: VintedClient) -> None:
  respx.get(HOME).mock(return_value=home_ok())
  respx.get(USER).mock(return_value=httpx.Response(200, json={"user": {"id": 42}}))
  assert await client.fetch_user("at", 42) == {"user": {"id": 42}}


@respx.mock
async def test_fetch_item_page(client: VintedClient, limiter: CountingLimiter) -> None:
  route = respx.get("https://www.vinted.at/items/5").mock(
    return_value=httpx.Response(200, text="<html>ok</html>")
  )
  assert await client.fetch_item_page("at", 5) == "<html>ok</html>"
  assert route.calls.last.request.headers["User-Agent"] == UA
  assert limiter.calls == 1  # keine Session nötig, aber durch den Limiter


@respx.mock
async def test_fetch_item_page_403(client: VintedClient) -> None:
  respx.get("https://www.vinted.at/items/5").mock(return_value=httpx.Response(403))
  with pytest.raises(BlockedError):
    await client.fetch_item_page("at", 5)


@respx.mock
async def test_item_page_is_low_priority(client: VintedClient, limiter: CountingLimiter) -> None:
  respx.get("https://www.vinted.at/items/5").mock(return_value=httpx.Response(200, text="x"))
  await client.fetch_item_page("at", 5)
  assert limiter.low_priority_calls == 1


@respx.mock
async def test_oversized_response_is_rejected(
  client: VintedClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setattr("vinted_bot.vinted.client.MAX_RESPONSE_BYTES", 1000)
  respx.get("https://www.vinted.at/items/5").mock(return_value=httpx.Response(200, text="x" * 5000))
  with pytest.raises(MalformedResponseError, match="größer"):
    await client.fetch_item_page("at", 5)
  assert client.stats.summary().by_outcome == {"too_large": 1}


@respx.mock
async def test_gzip_compressed_responses_are_decoded_once(client: VintedClient) -> None:
  """Regression: Vinted antwortet komprimiert – das darf nicht doppelt entpackt werden."""
  import gzip
  import json

  body = gzip.compress(json.dumps({"items": [{"id": 1}]}).encode())
  respx.get(HOME).mock(
    return_value=httpx.Response(
      200,
      headers={"set-cookie": "access_token_web=t; Path=/", "content-encoding": "gzip"},
      content=gzip.compress(b"<html></html>"),
    )
  )
  respx.get(CATALOG).mock(
    return_value=httpx.Response(200, headers={"content-encoding": "gzip"}, content=body)
  )
  assert await client.fetch_catalog("at", {}) == [{"id": 1}]
