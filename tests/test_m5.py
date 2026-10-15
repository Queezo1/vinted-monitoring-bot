"""M5: Domain-Sperre, Request-Statistik, Admin-Meldungen, /status, Aufräumen."""

import asyncio
from datetime import timedelta

from tests.conftest import FakeClock, make_item
from vinted_bot.core.dispatcher import Dispatcher, PermanentSendError
from vinted_bot.core.domain_guard import (
  BLOCKED_MAX,
  BLOCKED_START,
  RATE_LIMITED_MAX,
  DomainGuard,
)
from vinted_bot.core.notifications import Severity, StatusEvent
from vinted_bot.discord_bot.admin import AdminNotifier, status_embed
from vinted_bot.discord_bot.status import StatusData, build_status_embed
from vinted_bot.storage.repository import NewItem, OutboxEntry, Repository
from vinted_bot.vinted.errors import BlockedError, RateLimitedError
from vinted_bot.vinted.stats import RequestStats, StatsSummary

# --- DomainGuard ----------------------------------------------------------------


def test_403_escalates_up_to_six_hours(clock: FakeClock) -> None:
  guard = DomainGuard(clock)
  durations = []
  for _ in range(8):
    block = guard.report_error("at", BlockedError("403"))
    durations.append(block.until - clock.now)
    clock.now = block.until  # Sperre abwarten
  assert durations[:3] == [BLOCKED_START, BLOCKED_START * 2, BLOCKED_START * 4]
  assert durations[-1] == BLOCKED_MAX


def test_parallel_errors_do_not_double_escalate(clock: FakeClock) -> None:
  guard = DomainGuard(clock)
  first = guard.report_error("at", BlockedError("403"))
  second = guard.report_error("at", BlockedError("403"))  # anderer Poller, gleiche Sperre
  assert second == first and second.level == 1


def test_429_respects_retry_after_and_cap(clock: FakeClock) -> None:
  guard = DomainGuard(clock)
  block = guard.report_error("at", RateLimitedError("429", retry_after_s=600))
  assert block.until - clock.now == timedelta(minutes=10)
  assert block.reason == "rate_limited"
  for _ in range(6):
    clock.now = block.until
    block = guard.report_error("at", RateLimitedError("429", retry_after_s=None))
  assert block.until - clock.now == RATE_LIMITED_MAX


def test_domains_are_independent_and_success_resets(clock: FakeClock) -> None:
  guard = DomainGuard(clock)
  started = clock.now
  guard.report_error("at", BlockedError("403"))
  assert guard.blocked_until("de") is None
  assert guard.report_success("de") is None  # war nie gestört
  recovery = guard.report_success("at")
  assert recovery is not None and recovery.since == started
  assert guard.current("at") is None
  # Nach Erfolg beginnt die Eskalation wieder bei Stufe 1
  assert guard.report_error("at", BlockedError("403")).level == 1


# --- RequestStats ---------------------------------------------------------------


def test_stats_window() -> None:
  now = [0.0]
  stats = RequestStats(clock=lambda: now[0])
  stats.record("catalog", "200")
  stats.record("user", "403")
  now[0] = 1800
  stats.record("catalog", "200")
  assert stats.summary().total == 3
  now[0] = 3700  # die ersten beiden sind älter als 1 h
  summary = stats.summary()
  assert summary.total == 1
  assert summary.by_kind == {"catalog": 1}
  assert summary.by_outcome == {"200": 1}


# --- AdminNotifier ----------------------------------------------------------------


def event(key: str, severity: Severity = Severity.WARNING) -> StatusEvent:
  return StatusEvent(severity, "Titel", "Text", key=key)


async def test_notifier_debounces_same_key(clock: FakeClock) -> None:
  delivered: list[str] = []

  async def deliver(e: StatusEvent) -> None:
    delivered.append(e.key)

  notifier = AdminNotifier(deliver, now=clock, cooldown=timedelta(minutes=30))
  notifier.post(event("a"))
  notifier.post(event("a"))  # entprellt
  notifier.post(event("b"))
  clock.now += timedelta(minutes=31)
  notifier.post(event("a"))  # Abkühlzeit vorbei
  assert notifier.pending() == 3

  task = asyncio.create_task(notifier.run())
  while notifier.pending():
    await asyncio.sleep(0)
  await asyncio.sleep(0)
  task.cancel()
  assert delivered == ["a", "b", "a"]


async def test_failing_delivery_does_not_kill_notifier(clock: FakeClock) -> None:
  delivered: list[str] = []

  async def deliver(e: StatusEvent) -> None:
    if e.key == "boom":
      raise RuntimeError("Discord weg")
    delivered.append(e.key)

  notifier = AdminNotifier(deliver, now=clock)
  notifier.post(event("boom"))
  notifier.post(event("ok"))
  task = asyncio.create_task(notifier.run())
  for _ in range(5):
    await asyncio.sleep(0)
  task.cancel()
  assert delivered == ["ok"]


def test_status_embed_colors() -> None:
  assert status_embed(event("x", Severity.ERROR)).title == "🔴 Titel"
  assert status_embed(event("x", Severity.INFO)).color is not None


# --- /status ----------------------------------------------------------------------


def test_status_embed_shows_blocks_and_stats(clock: FakeClock) -> None:
  guard = DomainGuard(clock)
  block = guard.report_error("at", BlockedError("403"))
  embed = build_status_embed(
    StatusData(
      started_at=clock.now,
      filters=[],
      blocks={"at": block, "de": None},
      stats=StatsSummary(
        total=12, by_kind={"catalog": 10, "user": 2}, by_outcome={"200": 11, "403": 1}
      ),
      pending=0,
      posted_24h=7,
      admin_channel_id=1,
    )
  )
  text = "\n".join(f"{f.name}: {f.value}" for f in embed.fields)
  assert embed.title is not None and "eingeschränkt" in embed.title
  assert "⛔ vinted.at: 403 abgewiesen (Stufe 1)" in text
  assert "✅ vinted.de: erreichbar" in text
  assert "12 gesamt (Katalog 10 · Verkäufer 2)" in text
  assert "Fehler: 403: 1" in text
  assert "7 · 0 wartend" in text


def test_status_embed_all_good(clock: FakeClock) -> None:
  embed = build_status_embed(StatusData(clock.now, [], {}, StatsSummary(0, {}, {}), 0, 0, None))
  assert embed.title == "✅ Vinted-Bot läuft"


# --- Dispatcher: Kanal unbrauchbar -------------------------------------------------


async def test_permanent_failure_callback(repo: Repository) -> None:
  filter_id = await repo.add_filter(
    name="f", source_url="x", canonical_url="x", domain="at", api_params={}, channel_id=1,
    countries=(), interval_s=60,
  )  # fmt: skip
  await repo.save_poll_result(filter_id, new_items=[NewItem(make_item(1), None)], max_seen_id=1)
  reported: list[tuple[int, str]] = []

  async def send(entry: OutboxEntry) -> None:
    raise PermanentSendError("Kanal gelöscht")

  async def on_failure(entry: OutboxEntry, reason: str) -> None:
    reported.append((entry.filter_id, reason))

  await Dispatcher(repo, send, on_permanent_failure=on_failure).flush_once()
  assert reported == [(filter_id, "Kanal gelöscht")]


# --- Aufräumen & Zähler ------------------------------------------------------------


async def test_cleanup_and_counts(repo: Repository, clock: FakeClock) -> None:
  filter_id = await repo.add_filter(
    name="f", source_url="x", canonical_url="x", domain="at", api_params={}, channel_id=1,
    countries=(), interval_s=60,
  )  # fmt: skip
  await repo.save_poll_result(filter_id, baseline_ids=[1], max_seen_id=1)
  await repo.save_poll_result(filter_id, new_items=[NewItem(make_item(2), None)], max_seen_id=2)
  await repo.mark_posted(filter_id, 2)
  assert await repo.count_posted_since(clock.now - timedelta(hours=1)) == 1

  clock.now += timedelta(days=31)
  await repo.save_poll_result(filter_id, new_items=[NewItem(make_item(3), None)], max_seen_id=3)
  assert await repo.count_pending() == 1

  removed = await repo.cleanup(
    items_older_than=timedelta(days=30), sellers_older_than=timedelta(days=60)
  )
  assert removed == 2  # Artikel 1 und 2; 3 ist neu und noch ungesendet
  assert await repo.seen_item_ids(filter_id) == {3}
