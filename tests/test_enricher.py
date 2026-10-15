"""Enricher: nach dem Posten Fotos/Beschreibung/Verkäufer nachladen und Nachricht ergänzen."""

from datetime import timedelta
from typing import Any

from tests.conftest import FakeClock, make_item
from tests.test_poller import FakeVinted
from vinted_bot.core.enricher import MAX_JOB_AGE, PAUSE_AFTER_BLOCK, Enricher, EnrichJob
from vinted_bot.core.notifications import NullNotifier
from vinted_bot.core.seller_lookup import SellerResolver
from vinted_bot.storage.repository import OutboxEntry, Repository
from vinted_bot.vinted.errors import BlockedError


def entry(item_id: int = 11, seller_id: int = 1, **item_overrides: Any) -> OutboxEntry:
  return OutboxEntry(
    filter_id=1,
    item_id=item_id,
    channel_id=1,
    filter_name="f",
    domain="at",
    item=make_item(item_id, seller_id=seller_id, title=f"Pullover {item_id}", **item_overrides),
    seller=None,
    first_seen_at=FakeClock().now,
  )


def build(repo: Repository, clock: FakeClock, **options: Any) -> tuple[Enricher, FakeVinted, list]:
  fake = FakeVinted()
  edits: list[tuple[Any, OutboxEntry]] = []

  async def edit(handle: Any, updated: OutboxEntry) -> None:
    edits.append((handle, updated))

  settings = {"seller_lookup": True, "item_details": True, **options}
  enricher = Enricher(
    fake,  # type: ignore[arg-type]
    SellerResolver(repo, fake),  # type: ignore[arg-type]
    edit,
    now=clock,
    **settings,
  )
  return enricher, fake, edits


async def test_adds_gallery_description_and_seller(repo: Repository, clock: FakeClock) -> None:
  enricher, fake, edits = build(repo, clock)
  assert await enricher.process(EnrichJob(entry(), "nachricht-1", clock.now))

  [(handle, updated)] = edits
  assert handle == "nachricht-1"
  assert updated.item.photo_urls[:3] == tuple(f"https://img/11-{n}.webp" for n in (1, 2, 3))
  assert updated.item.description == "Beschreibung 11"
  assert updated.seller is not None and updated.seller.country_iso == "AT"
  assert fake.user_calls == [1] and fake.page_calls == [11]


async def test_nothing_to_do_without_options(repo: Repository, clock: FakeClock) -> None:
  enricher, fake, edits = build(repo, clock, seller_lookup=False, item_details=False)
  enricher.submit(entry(), "x")
  assert enricher.pending() == 0  # gar nicht erst eingereiht


async def test_old_jobs_are_skipped(repo: Repository, clock: FakeClock) -> None:
  enricher, fake, edits = build(repo, clock)
  job = EnrichJob(entry(), "x", clock.now)
  clock.now += MAX_JOB_AGE + timedelta(seconds=1)
  assert not await enricher.process(job)
  assert fake.page_calls == [] and edits == []


async def test_block_pauses_enrichment_but_keeps_partial_data(
  repo: Repository, clock: FakeClock
) -> None:
  notifier = NullNotifier()
  throttled: list[int] = []
  enricher, fake, edits = build(repo, clock)
  enricher._notifier = notifier
  enricher._on_throttled = lambda: throttled.append(1)
  fake.page_error = BlockedError("403")

  assert await enricher.process(EnrichJob(entry(), "x", clock.now))  # Verkäufer kam noch an
  assert edits[0][1].seller is not None and edits[0][1].item.photo_urls == ()
  assert throttled == [1] and notifier.events[0].key == "enrich:at"

  # Während der Pause keine weiteren Anfragen
  assert not await enricher.process(EnrichJob(entry(12, seller_id=2), "y", clock.now))
  assert fake.page_calls == [11]
  clock.now += PAUSE_AFTER_BLOCK + timedelta(seconds=1)
  fake.page_error = None
  assert await enricher.process(EnrichJob(entry(13, seller_id=3), "z", clock.now))
