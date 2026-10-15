"""Der Discord-Client: verdrahtet Datenbank, Vinted-Client, Poller und Dispatcher.

Lebenszyklus:
  setup_hook()  – läuft einmal nach dem Login, vor dem Gateway-Connect: alles aufbauen
  close()       – beim Beenden: Tasks stoppen, HTTP-Client und Datenbank schließen
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import aiosqlite
import discord
import httpx
from discord import app_commands

from vinted_bot.config import Settings
from vinted_bot.core.dispatcher import Dispatcher, PermanentSendError
from vinted_bot.core.filter_manager import FilterManager
from vinted_bot.core.filter_service import FilterService
from vinted_bot.core.poller import FilterPoller
from vinted_bot.core.seller_lookup import SellerResolver
from vinted_bot.discord_bot.commands import FilterCommands
from vinted_bot.discord_bot.embeds import build_message
from vinted_bot.storage.db import open_database
from vinted_bot.storage.repository import OutboxEntry, Repository
from vinted_bot.vinted.client import VintedClient
from vinted_bot.vinted.rate_limiter import GlobalRateLimiter
from vinted_bot.vinted.url_parser import parse_filter_url

log = logging.getLogger(__name__)

# Nach einem Ausfall keine Flut alter Artikel: Ungesendetes, das älter ist, wird verworfen
OUTBOX_MAX_AGE = timedelta(hours=1)


class VintedBot(discord.Client):
  def __init__(self, settings: Settings) -> None:
    # Keine privilegierten Intents nötig: wir lesen keine Nachrichten und keine Mitglieder
    super().__init__(intents=discord.Intents.default())
    self.tree = app_commands.CommandTree(self)
    self.settings = settings
    self._conn: aiosqlite.Connection | None = None
    self._http_client: httpx.AsyncClient | None = None
    self._dispatcher_task: asyncio.Task[None] | None = None
    self.repo: Repository
    self.filters: FilterManager

  async def setup_hook(self) -> None:
    settings = self.settings
    self._conn = await open_database(settings.db_path)
    self.repo = Repository(self._conn)

    self._http_client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    vinted = VintedClient(
      self._http_client,
      GlobalRateLimiter(settings.global_min_spacing_s),
      settings.vinted_user_agent,
    )
    sellers = SellerResolver(self.repo, vinted)

    dispatcher = Dispatcher(self.repo, self._send_entry)

    def make_poller(filter_id: int) -> FilterPoller:
      return FilterPoller(
        filter_id,
        repo=self.repo,
        client=vinted,
        sellers=sellers,
        on_new_items=dispatcher.wake,
        seller_lookup=settings.seller_lookup,
        item_details=settings.item_details,
      )

    self.filters = FilterManager(make_poller)
    service = FilterService(self.repo, self.filters, settings.default_interval_s)

    # Slash-Commands nur für DEINEN Server registrieren: sofort verfügbar (global dauert's)
    guild = discord.Object(id=settings.guild_id)
    self.tree.add_command(FilterCommands(self, service, self.repo), guild=guild)
    synced = await self.tree.sync(guild=guild)
    log.info("Slash-Commands synchronisiert: %s", ", ".join(c.name for c in synced))

    await self._ensure_seed_filter()
    expired = await self.repo.expire_stale_outbox(OUTBOX_MAX_AGE)
    if expired:
      log.info("%s veraltete, ungesendete Artikel verworfen", expired)

    active = await self.repo.list_filters(only_active=True)
    log.info("Starte %s aktive(n) Filter: %s", len(active), ", ".join(f.name for f in active))
    self.filters.start_all([f.id for f in active])
    self._dispatcher_task = asyncio.create_task(self._run_dispatcher(dispatcher), name="dispatcher")

  async def on_ready(self) -> None:
    log.info("Eingeloggt als %s (ID %s)", self.user, self.user.id if self.user else "?")

  async def close(self) -> None:
    log.info("Fahre herunter …")
    if hasattr(self, "filters"):
      await self.filters.stop_all()
    if self._dispatcher_task is not None:
      self._dispatcher_task.cancel()
      await asyncio.gather(self._dispatcher_task, return_exceptions=True)
    if self._http_client is not None:
      await self._http_client.aclose()
    if self._conn is not None:
      await self._conn.close()
    await super().close()

  # --- Interna ---------------------------------------------------------------

  async def _ensure_seed_filter(self) -> None:
    """Ersteinrichtung: legt den Filter aus der .env an – nur wenn es noch GAR KEINE Filter gibt.

    Seit M3 werden Filter per /filter add verwaltet. Würden wir hier jedes Mal vergleichen,
    entstünde nach einer Änderung per Command (z. B. andere Länder) ein Duplikat.
    """
    settings = self.settings
    if not settings.seed_filter_url or settings.seed_channel_id is None:
      return
    if await self.repo.list_filters():
      return
    parsed = parse_filter_url(settings.seed_filter_url)
    filter_id = await self.repo.add_filter(
      name=settings.seed_filter_name,
      source_url=settings.seed_filter_url,
      canonical_url=parsed.canonical_url,
      domain=parsed.domain,
      api_params=parsed.api_params,
      channel_id=settings.seed_channel_id,
      countries=settings.seed_filter_countries,
      interval_s=settings.default_interval_s,
    )
    log.info(
      "Start-Filter %s angelegt (%s, Länder: %s)",
      filter_id,
      parsed.canonical_url,
      ",".join(settings.seed_filter_countries) or "alle",
    )
    if parsed.ignored:
      log.warning("Im Start-Filter ignorierte Parameter: %s", ", ".join(parsed.ignored))

  async def _run_dispatcher(self, dispatcher: Dispatcher) -> None:
    # Erst senden, wenn die Gateway-Verbindung steht (Kanal-Cache gefüllt)
    await self.wait_until_ready()
    await dispatcher.run()

  async def _send_entry(self, entry: OutboxEntry) -> None:
    channel = self.get_channel(entry.channel_id)
    if channel is None:
      try:
        channel = await self.fetch_channel(entry.channel_id)
      except (discord.NotFound, discord.Forbidden) as error:
        raise PermanentSendError(f"Kanal {entry.channel_id} nicht erreichbar: {error}") from error
    if not isinstance(channel, discord.abc.Messageable):
      raise PermanentSendError(f"Kanal {entry.channel_id} ist kein Textkanal")

    embeds, view = build_message(entry)
    try:
      await channel.send(embeds=embeds, view=view)
    except discord.Forbidden as error:
      raise PermanentSendError(f"Keine Rechte in Kanal {entry.channel_id}: {error}") from error
    log.info("Gepostet: %s (Filter %s)", entry.item.title, entry.filter_name)
