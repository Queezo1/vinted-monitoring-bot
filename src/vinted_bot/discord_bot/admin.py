"""Admin-Kanal #vinted-status: Statusmeldungen bei Sperren, Fehlern und Erholung.

- Entprellung: Dieselbe Meldung (gleicher `key`) kommt höchstens einmal pro 30 min.
- Nicht blockierend: `post()` legt nur in eine Warteschlange, `run()` sendet nacheinander.
- Kanal: ADMIN_CHANNEL_ID aus der .env, sonst wird #vinted-status in der Kategorie
  "Vinted" angelegt und seine ID gespeichert.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import discord

from vinted_bot.core.notifications import Severity, StatusEvent
from vinted_bot.discord_bot.channels import get_or_create_category
from vinted_bot.storage.repository import Repository, utc_now

log = logging.getLogger(__name__)

COOLDOWN = timedelta(minutes=30)
ADMIN_CHANNEL_NAME = "vinted-status"
ADMIN_CHANNEL_KEY = "admin_channel_id"

_COLORS = {Severity.INFO: 0x2ECC71, Severity.WARNING: 0xF39C12, Severity.ERROR: 0xE74C3C}
_ICONS = {Severity.INFO: "🟢", Severity.WARNING: "🟠", Severity.ERROR: "🔴"}
_LOG_LEVELS = {
  Severity.INFO: logging.INFO,
  Severity.WARNING: logging.WARNING,
  Severity.ERROR: logging.ERROR,
}


class AdminNotifier:
  def __init__(
    self,
    deliver: Callable[[StatusEvent], Awaitable[None]],
    *,
    now: Callable[[], datetime] = utc_now,
    cooldown: timedelta = COOLDOWN,
  ) -> None:
    self._deliver = deliver
    self._now = now
    self._cooldown = cooldown
    self._queue: asyncio.Queue[StatusEvent] = asyncio.Queue()
    self._last_sent: dict[str, datetime] = {}

  def post(self, event: StatusEvent) -> None:
    log.log(_LOG_LEVELS[event.severity], "Statusmeldung: %s – %s", event.title, event.text)
    now = self._now()
    last = self._last_sent.get(event.key)
    if last is not None and now - last < self._cooldown:
      log.debug("Meldung '%s' entprellt", event.key)
      return
    self._last_sent[event.key] = now
    self._queue.put_nowait(event)

  def pending(self) -> int:
    return self._queue.qsize()

  async def run(self) -> None:
    while True:
      event = await self._queue.get()
      try:
        await self._deliver(event)
      except Exception:
        # Eine Statusmeldung darf nie den Bot stören – steht ja auch im Log
        log.exception("Statusmeldung konnte nicht gesendet werden")


def status_embed(event: StatusEvent) -> discord.Embed:
  return discord.Embed(
    title=f"{_ICONS[event.severity]} {event.title}"[:256],
    description=event.text[:4096],
    color=_COLORS[event.severity],
    timestamp=utc_now(),
  )


async def resolve_admin_channel(
  guild: discord.Guild, repo: Repository, configured_id: int | None
) -> discord.TextChannel:
  """Kanal aus der .env, sonst gespeicherter, sonst neu angelegter #vinted-status."""
  for channel_id in (configured_id, await _stored_id(repo)):
    if channel_id is None:
      continue
    channel = guild.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
      return channel
  category = await get_or_create_category(guild, repo)
  channel = discord.utils.get(category.text_channels, name=ADMIN_CHANNEL_NAME)
  if channel is None:
    channel = await guild.create_text_channel(
      ADMIN_CHANNEL_NAME,
      category=category,
      topic="Statusmeldungen des Vinted-Bots (Sperren, Fehler, Neustarts)",
      position=0,
      reason="Vinted-Bot: Admin-Kanal",
    )
  await repo.set_value(ADMIN_CHANNEL_KEY, str(channel.id))
  return channel


async def _stored_id(repo: Repository) -> int | None:
  value = await repo.get_value(ADMIN_CHANNEL_KEY)
  return int(value) if value else None
