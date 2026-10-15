"""Poller + echte SQLite (in-memory) + gefälschter Vinted-Client."""

from typing import Any

from tests.conftest import raw_item
from vinted_bot.core.poller import BACKOFF_BLOCKED, FilterPoller
from vinted_bot.core.seller_lookup import SellerResolver
from vinted_bot.storage.repository import Repository
from vinted_bot.vinted.errors import BlockedError, VintedError

COUNTRY_OF_SELLER = {1: "AT", 2: "DE", 3: "IT"}


class FakeVinted:
  """Gibt vorbereitete Kataloge zurück und zählt Verkäufer-Abfragen."""

  def __init__(self) -> None:
    self.catalogs: list[list[dict[str, Any]] | VintedError] = []
    self.user_calls: list[int] = []
    self.user_error: VintedError | None = None
    self.page_calls: list[int] = []
    self.page_error: VintedError | None = None

  async def fetch_catalog(self, domain: str, params: dict[str, str]) -> list[dict[str, Any]]:
    result = self.catalogs.pop(0)
    if isinstance(result, VintedError):
      raise result
    return result

  async def fetch_user(self, domain: str, user_id: int) -> dict[str, Any]:
    self.user_calls.append(user_id)
    if self.user_error:
      raise self.user_error
    country = COUNTRY_OF_SELLER.get(user_id)
    return {"user": {"id": user_id, "country_iso_code": country, "feedback_reputation": 1.0,
                     "feedback_count": 10}}  # fmt: skip

  async def fetch_item_page(self, domain: str, item_id: int) -> str:
    self.page_calls.append(item_id)
    if self.page_error:
      raise self.page_error
    photos = ",".join(f'{{"url":"https://img/{item_id}-{n}.webp"}}' for n in range(1, 5))
    return (
      f'<meta property="og:description" content="Pullover {item_id} - Beschreibung {item_id}">'
      f'{{"item_id":"{item_id}","photos":[{photos}]}}'
    )


async def setup(
  repo: Repository, countries: tuple[str, ...] = ()
) -> tuple[FilterPoller, FakeVinted, list[int]]:
  filter_id = await repo.add_filter(
    name="test",
    source_url="x",
    canonical_url="x",
    domain="at",
    api_params={},
    channel_id=1,
    countries=countries,
    interval_s=60,
  )
  return make_poller(repo, filter_id)


def make_poller(repo: Repository, filter_id: int) -> tuple[FilterPoller, FakeVinted, list[int]]:
  fake = FakeVinted()
  wakeups: list[int] = []
  poller = FilterPoller(
    filter_id,
    repo=repo,
    client=fake,  # type: ignore[arg-type]
    sellers=SellerResolver(repo, fake),  # type: ignore[arg-type]
    on_new_items=lambda: wakeups.append(1),
    rng=lambda: 0.5,  # kein Jitter im Test
  )
  return poller, fake, wakeups


async def poll(poller: FilterPoller, repo: Repository) -> float:
  flt = await repo.get_filter(poller.filter_id)
  assert flt is not None
  return await poller.poll_once(flt)


async def outbox_ids(repo: Repository) -> list[int]:
  return [entry.item_id for entry in await repo.pending_outbox()]


async def test_baseline_then_only_new_items(repo: Repository) -> None:
  poller, fake, wakeups = await setup(repo)
  fake.catalogs = [
    [raw_item(102), raw_item(101)],
    [raw_item(104), raw_item(103), raw_item(102), raw_item(101)],
  ]
  assert await poll(poller, repo) == 60  # Intervall, ohne Jitter
  assert await outbox_ids(repo) == []
  assert fake.user_calls == []  # Baseline kostet keine Verkäufer-Requests

  await poll(poller, repo)
  assert await outbox_ids(repo) == [103, 104]  # älteste zuerst
  assert len(wakeups) == 1


async def test_restart_does_not_repost(repo: Repository) -> None:
  poller, fake, _ = await setup(repo)
  fake.catalogs = [[raw_item(1)], [raw_item(2), raw_item(1)]]
  await poll(poller, repo)
  await poll(poller, repo)

  # "Neustart": neuer Poller, gleiche Datenbank, Vinted liefert dasselbe
  restarted, fake2, _ = make_poller(repo, poller.filter_id)
  fake2.catalogs = [[raw_item(2), raw_item(1)]]
  await poll(restarted, repo)
  assert await outbox_ids(repo) == [2]


async def test_country_filter(repo: Repository) -> None:
  poller, fake, _ = await setup(repo, countries=("AT", "DE"))
  fake.catalogs = [
    [raw_item(10)],
    [raw_item(13, seller_id=3), raw_item(12, seller_id=2), raw_item(11, seller_id=1)],
  ]
  await poll(poller, repo)
  await poll(poller, repo)
  assert await outbox_ids(repo) == [11, 12]  # IT-Verkäufer 3 gefiltert
  assert sorted(fake.user_calls) == [1, 2, 3]


async def test_seller_cache_avoids_repeated_lookups(repo: Repository) -> None:
  poller, fake, _ = await setup(repo)
  fake.catalogs = [[], [raw_item(2, seller_id=1)], [raw_item(3, seller_id=1)]]
  for _ in range(3):
    await poll(poller, repo)
  assert fake.user_calls == [1]


async def test_unknown_country_is_still_posted(repo: Repository) -> None:
  poller, fake, _ = await setup(repo, countries=("AT",))
  fake.catalogs = [[], [raw_item(2, seller_id=99)]]  # 99: kein Land bekannt
  await poll(poller, repo)
  await poll(poller, repo)
  assert await outbox_ids(repo) == [2]


async def test_blocked_seller_lookup_stops_lookups_but_posts(repo: Repository) -> None:
  poller, fake, _ = await setup(repo)
  fake.user_error = BlockedError("403")
  fake.catalogs = [[], [raw_item(3, seller_id=1), raw_item(2, seller_id=2)]]
  await poll(poller, repo)
  await poll(poller, repo)
  assert await outbox_ids(repo) == [2, 3]
  assert len(fake.user_calls) == 1  # nach dem ersten 403 keine weiteren Versuche


async def test_blocked_catalog_backs_off_and_escalates(repo: Repository) -> None:
  poller, fake, _ = await setup(repo)
  fake.catalogs = [BlockedError("403"), BlockedError("403"), [raw_item(1)]]
  assert await poll(poller, repo) == BACKOFF_BLOCKED[0]
  assert await poll(poller, repo) == BACKOFF_BLOCKED[0] * 2
  assert await poll(poller, repo) == 60  # Erfolg setzt den Backoff zurück
  flt = await repo.get_filter(poller.filter_id)
  assert flt is not None and flt.baseline_done


async def test_run_stops_when_filter_paused(repo: Repository) -> None:
  poller, _, _ = await setup(repo)
  await repo._conn.execute("UPDATE filters SET paused = 1")
  await poller.run()  # muss sofort zurückkehren statt endlos zu laufen


async def test_item_details_only_for_posted_items(repo: Repository) -> None:
  poller, fake, _ = await setup(repo, countries=("AT",))
  fake.catalogs = [[], [raw_item(12, seller_id=3), raw_item(11, seller_id=1)]]  # 3 = IT
  await poll(poller, repo)
  await poll(poller, repo)
  assert fake.page_calls == [11]  # IT-Artikel wurde gefiltert -> keine Artikelseite geladen
  [entry] = await repo.pending_outbox()
  assert entry.item.photo_urls[:2] == ("https://img/11-1.webp", "https://img/11-2.webp")
  assert entry.item.description == "Beschreibung 11"


async def test_item_page_failure_still_posts(repo: Repository) -> None:
  poller, fake, _ = await setup(repo)
  fake.page_error = BlockedError("403")
  fake.catalogs = [[], [raw_item(3), raw_item(2)]]
  await poll(poller, repo)
  await poll(poller, repo)
  assert await outbox_ids(repo) == [2, 3]
  assert len(fake.page_calls) == 1  # nach 403 keine weiteren Seitenabrufe
