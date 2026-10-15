from tests.conftest import make_item
from vinted_bot.core.dispatcher import Dispatcher, PermanentSendError
from vinted_bot.storage.repository import NewItem, OutboxEntry, Repository


async def prepare(repo: Repository, item_ids: list[int]) -> int:
  filter_id = await repo.add_filter(
    name="f",
    source_url="x",
    canonical_url="x",
    domain="at",
    api_params={},
    channel_id=1,
    countries=(),
    interval_s=60,
  )
  await repo.save_poll_result(
    filter_id, new_items=[NewItem(make_item(i), None) for i in item_ids], max_seen_id=None
  )
  return filter_id


async def test_sends_in_order_and_marks_posted(repo: Repository) -> None:
  await prepare(repo, [1, 2])
  sent: list[int] = []

  async def send(entry: OutboxEntry) -> None:
    sent.append(entry.item_id)

  assert await Dispatcher(repo, send).flush_once() == 2
  assert sent == [1, 2]
  assert await repo.pending_outbox() == []


async def test_temporary_error_keeps_entry_and_stops_batch(repo: Repository) -> None:
  await prepare(repo, [1, 2])
  sent: list[int] = []

  async def send(entry: OutboxEntry) -> None:
    if entry.item_id == 1:
      raise RuntimeError("Discord kurz weg")
    sent.append(entry.item_id)

  assert await Dispatcher(repo, send).flush_once() == 0
  assert sent == []  # nach Fehler nicht sofort weiterfeuern
  assert [e.item_id for e in await repo.pending_outbox()] == [1, 2]


async def test_permanent_error_drops_entry(repo: Repository) -> None:
  await prepare(repo, [1, 2])

  async def send(entry: OutboxEntry) -> None:
    if entry.item_id == 1:
      raise PermanentSendError("Kanal gelöscht")

  assert await Dispatcher(repo, send).flush_once() == 1
  assert await repo.pending_outbox() == []


async def test_pending_entries_survive_a_crash(repo: Repository) -> None:
  """Gespeichert, aber vor dem Senden abgestürzt -> nach Neustart wird nachgeholt."""
  await prepare(repo, [1])
  sent: list[int] = []

  async def send(entry: OutboxEntry) -> None:
    sent.append(entry.item_id)

  await Dispatcher(repo, send).flush_once()  # "neuer Prozess"
  assert sent == [1]
