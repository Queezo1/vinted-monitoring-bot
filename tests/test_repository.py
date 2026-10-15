from datetime import timedelta

from tests.conftest import FakeClock, make_item
from vinted_bot.storage.repository import NewItem, Repository
from vinted_bot.vinted.models import SellerInfo


async def add_filter(repo: Repository, *, countries: tuple[str, ...] = ()) -> int:
  return await repo.add_filter(
    name="pullover",
    source_url="https://www.vinted.at/catalog?brand_ids[]=88",
    canonical_url="https://www.vinted.at/catalog?attribute_ids[brand]=88",
    domain="at",
    api_params={"attribute_ids[brand]": "88"},
    channel_id=555,
    countries=countries,
    interval_s=60,
  )


async def test_filter_roundtrip(repo: Repository) -> None:
  filter_id = await add_filter(repo, countries=("AT", "DE"))
  flt = await repo.get_filter(filter_id)
  assert flt is not None
  assert flt.countries == ("AT", "DE")
  assert flt.api_params == {"attribute_ids[brand]": "88"}
  assert flt.baseline_done is False
  assert await repo.find_filter(flt.canonical_url, ("AT", "DE"), 555) == flt
  assert await repo.find_filter(flt.canonical_url, (), 555) is None


async def test_same_link_with_and_without_countries_are_different_filters(repo: Repository) -> None:
  await add_filter(repo)
  await add_filter(repo, countries=("IT",))
  assert len(await repo.list_filters()) == 2


async def test_poll_result_and_outbox(repo: Repository) -> None:
  filter_id = await add_filter(repo)
  await repo.save_poll_result(filter_id, baseline_ids=[1, 2], max_seen_id=2)
  seller = SellerInfo(5, "DE", 1.0, 14, "https://a/b.jpg")
  await repo.save_poll_result(
    filter_id,
    new_items=[NewItem(make_item(3), seller), NewItem(make_item(4), None)],
    skipped_ids=[5],
    max_seen_id=5,
  )

  assert await repo.seen_item_ids(filter_id) == {1, 2, 3, 4, 5}
  flt = await repo.get_filter(filter_id)
  assert flt is not None and flt.baseline_done and flt.max_seen_id == 5

  outbox = await repo.pending_outbox()
  assert [entry.item_id for entry in outbox] == [3, 4]
  assert outbox[0].item == make_item(3)
  assert outbox[0].seller == seller
  assert outbox[0].channel_id == 555
  assert outbox[1].seller is None

  await repo.mark_posted(filter_id, 3)
  assert [entry.item_id for entry in await repo.pending_outbox()] == [4]


async def test_seen_item_is_never_overwritten(repo: Repository) -> None:
  filter_id = await add_filter(repo)
  await repo.save_poll_result(filter_id, baseline_ids=[1], max_seen_id=1)
  await repo.save_poll_result(filter_id, new_items=[NewItem(make_item(1), None)], max_seen_id=1)
  assert await repo.pending_outbox() == []


async def test_send_failures_give_up_after_max_attempts(repo: Repository) -> None:
  filter_id = await add_filter(repo)
  await repo.save_poll_result(filter_id, new_items=[NewItem(make_item(7), None)], max_seen_id=7)
  assert await repo.mark_send_failed(filter_id, 7, max_attempts=2) is False
  assert len(await repo.pending_outbox()) == 1
  assert await repo.mark_send_failed(filter_id, 7, max_attempts=2) is True
  assert await repo.pending_outbox() == []


async def test_stale_outbox_expires(repo: Repository, clock: FakeClock) -> None:
  filter_id = await add_filter(repo)
  await repo.save_poll_result(filter_id, new_items=[NewItem(make_item(7), None)], max_seen_id=7)
  clock.now += timedelta(hours=2)
  assert await repo.expire_stale_outbox(timedelta(hours=1)) == 1
  assert await repo.pending_outbox() == []


async def test_error_counter(repo: Repository) -> None:
  filter_id = await add_filter(repo)
  assert await repo.record_poll_error(filter_id, "BlockedError") == 1
  assert await repo.record_poll_error(filter_id, "BlockedError") == 2
  await repo.save_poll_result(filter_id, max_seen_id=None)  # Erfolg setzt zurück
  assert await repo.record_poll_error(filter_id, "x") == 1


async def test_seller_cache_expires(repo: Repository, clock: FakeClock) -> None:
  seller = SellerInfo(9, "AT", 0.8, 3, None)
  await repo.save_seller(seller)
  assert await repo.get_seller(9, timedelta(days=30)) == seller
  clock.now += timedelta(days=31)
  assert await repo.get_seller(9, timedelta(days=30)) is None


async def test_filter_updates_and_delete_cascade(repo: Repository) -> None:
  filter_id = await add_filter(repo)
  await repo.set_paused(filter_id, True)
  await repo.set_countries(filter_id, ("IT",))
  await repo.set_interval(filter_id, 300)
  flt = await repo.get_filter(filter_id)
  assert flt is not None and flt.paused and flt.countries == ("IT",) and flt.interval_s == 300
  assert await repo.list_filters(only_active=True) == []

  await repo.save_poll_result(filter_id, baseline_ids=[1, 2], max_seen_id=2)
  assert [f.id for f in await repo.filters_in_channel(555)] == [filter_id]
  await repo.delete_filter(filter_id)
  assert await repo.get_filter(filter_id) is None
  assert await repo.seen_item_ids(filter_id) == set()  # ON DELETE CASCADE


async def test_status_fields(repo: Repository, clock: FakeClock) -> None:
  filter_id = await add_filter(repo)
  await repo.record_poll_error(filter_id, "BlockedError: 403")
  flt = await repo.get_filter(filter_id)
  assert flt is not None and flt.fail_count == 1 and flt.last_error == "BlockedError: 403"
  await repo.save_poll_result(filter_id, max_seen_id=None)
  flt = await repo.get_filter(filter_id)
  assert flt is not None and flt.last_success_at == clock.now and flt.last_error is None


async def test_key_value(repo: Repository) -> None:
  assert await repo.get_value("x") is None
  await repo.set_value("x", "1")
  await repo.set_value("x", "2")
  assert await repo.get_value("x") == "2"
