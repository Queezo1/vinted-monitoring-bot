"""Der Discord-Client: verdrahtet Datenbank, Vinted-Client, Poller, Dispatcher und Admin-Kanal.

Lebenszyklus:
  setup_hook()  – läuft einmal nach dem Login, vor dem Gateway-Connect: alles aufbauen
  on_ready()    – Verbindung steht: Startmeldung in den Admin-Kanal
  close()       – beim Beenden: Tasks stoppen, HTTP-Client und Datenbank schließen

Hintergrund-Tasks (neben einem Poller pro Filter):
  dispatcher    – postet neue Artikel (sofort, nur mit den Daten aus der Suche)
  enricher      – lädt danach Fotos/Beschreibung/Verkäuferinfos nach und ergänzt den Post
  admin         – sendet Statusmeldungen (#vinted-status)
  housekeeping  – räumt alle 6 h alte Daten auf
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import aiosqlite
import discord
import httpx
from discord import app_commands

from vinted_bot import __version__
from vinted_bot.config import Settings
from vinted_bot.core.dispatcher import Dispatcher, PermanentSendError
from vinted_bot.core.domain_guard import DomainGuard
from vinted_bot.core.enricher import Enricher
from vinted_bot.core.filter_manager import FilterManager
from vinted_bot.core.filter_service import FilterService
from vinted_bot.core.notifications import Severity, StatusEvent, quote_error
from vinted_bot.core.poller import FilterPoller
from vinted_bot.core.seller_lookup import SellerResolver
from vinted_bot.discord_bot.admin import AdminNotifier, resolve_admin_channel, status_embed
from vinted_bot.discord_bot.commands import FilterCommands
from vinted_bot.discord_bot.embeds import build_message
from vinted_bot.discord_bot.status import StatusData, make_status_command
from vinted_bot.storage.db import open_database
from vinted_bot.storage.repository import FilterRow, OutboxEntry, Repository, utc_now
from vinted_bot.vinted.client import VintedClient
from vinted_bot.vinted.rate_limiter import GlobalRateLimiter
from vinted_bot.vinted.url_parser import parse_filter_url

log = logging.getLogger(__name__)

# Nach einem Ausfall keine Flut alter Artikel: Ungesendetes, das älter ist, wird verworfen
OUTBOX_MAX_AGE = timedelta(hours=1)
HOUSEKEEPING_EVERY_S = 6 * 3600
KEEP_ITEMS = timedelta(days=30)
KEEP_SELLERS = timedelta(days=60)
# Anteil des Anfrage-Budgets für Katalog-Abfragen; der Rest bleibt für Länder-Lookups und
# das Nachladen. Bei 5 Filtern und 3 s Abstand: 5 × 3 s × 1,5 = 22,5 s pro Filter.
CATALOG_BUDGET_FACTOR = 1.5


class VintedBot(discord.Client):
  def __init__(self, settings: Settings) -> None:
    # Keine privilegierten Intents nötig: wir lesen keine Nachrichten und keine Mitglieder
    # AllowedMentions.none(): Der Bot pingt niemals jemanden, egal was in Texten steht
    super().__init__(
      intents=discord.Intents.default(), allowed_mentions=discord.AllowedMentions.none()
    )
    self.tree = app_commands.CommandTree(self)
    self.settings = settings
    self.started_at = utc_now()
    self.guard = DomainGuard()
    self.notifier = AdminNotifier(self._deliver_status)
    self._conn: aiosqlite.Connection | None = None
    self._http_client: httpx.AsyncClient | None = None
    self._tasks: list[asyncio.Task[None]] = []
    self._announced_start = False
    self.repo: Repository
    self.filters: FilterManager
    self.vinted: VintedClient

  async def setup_hook(self) -> None:
    settings = self.settings
    self._conn = await open_database(settings.db_path)
    self.repo = Repository(self._conn)

    self._http_client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    self.limiter = GlobalRateLimiter(settings.global_min_spacing_s)
    self.vinted = VintedClient(self._http_client, self.limiter, settings.vinted_user_agent)
    sellers = SellerResolver(self.repo, self.vinted)
    self.enricher = Enricher(
      self.vinted,
      sellers,
      self._edit_message,
      seller_lookup=settings.seller_lookup,
      item_details=settings.item_details,
      notifier=self.notifier,
      on_throttled=self.limiter.penalize,
    )
    dispatcher = Dispatcher(
      self.repo,
      self._send_entry,
      on_permanent_failure=self._on_channel_unusable,
      on_sent=self.enricher.submit,
    )

    def make_poller(filter_id: int) -> FilterPoller:
      return FilterPoller(
        filter_id,
        repo=self.repo,
        client=self.vinted,
        sellers=sellers,
        on_new_items=dispatcher.wake,
        interval_for=self.effective_interval,
        on_throttled=self.limiter.penalize,
        guard=self.guard,  # EIN Guard für alle Poller -> Sperren gelten domainweit
        notifier=self.notifier,
      )

    self.filters = FilterManager(make_poller)
    service = FilterService(self.repo, self.filters, settings.default_interval_s)

    # Slash-Commands nur für DEINEN Server registrieren: sofort verfügbar (global dauert's)
    guild = discord.Object(id=settings.guild_id)
    self.tree.add_command(FilterCommands(self, service, self.repo), guild=guild)
    self.tree.add_command(make_status_command(self._collect_status, settings.owner_id), guild=guild)
    synced = await self.tree.sync(guild=guild)
    log.info("Slash-Commands synchronisiert: %s", ", ".join(c.name for c in synced))

    await self._ensure_seed_filter()
    expired = await self.repo.expire_stale_outbox(OUTBOX_MAX_AGE)
    if expired:
      log.info("%s veraltete, ungesendete Artikel verworfen", expired)

    active = await self.repo.list_filters(only_active=True)
    log.info("Starte %s aktive(n) Filter: %s", len(active), ", ".join(f.name for f in active))
    self.filters.start_all([f.id for f in active])
    self._start_task(self._after_ready(dispatcher.run()), "dispatcher")
    self._start_task(self._after_ready(self.enricher.run()), "enricher")
    self._start_task(self._after_ready(self.notifier.run()), "admin")
    self._start_task(self._housekeeping(), "housekeeping")

  async def on_ready(self) -> None:
    log.info("Eingeloggt als %s (ID %s)", self.user, self.user.id if self.user else "?")
    # on_ready kann bei Verbindungsabbrüchen mehrfach kommen – nur einmal melden
    if not self._announced_start:
      self._announced_start = True
      filters = await self.repo.list_filters()
      active = sum(1 for f in filters if not f.paused)
      self.notifier.post(
        StatusEvent(
          Severity.INFO,
          "Bot gestartet",
          f"Version {__version__} · {active} aktive(r) Filter · Intervall Standard "
          f"{self.settings.default_interval_s} s · Abstand zu Vinted ≥ "
          f"{self.settings.global_min_spacing_s:g} s",
          key=f"start:{self.started_at.isoformat()}",
        )
      )

  async def close(self) -> None:
    log.info("Fahre herunter …")
    if hasattr(self, "filters"):
      await self.filters.stop_all()
    for task in self._tasks:
      task.cancel()
    await asyncio.gather(*self._tasks, return_exceptions=True)
    if self._http_client is not None:
      await self._http_client.aclose()
    if self._conn is not None:
      await self._conn.close()
    await super().close()

  def effective_interval(self, flt: FilterRow) -> float:
    """Eingestelltes Intervall – bei vielen Filtern gestreckt, damit das Budget reicht.

    Alle Filter teilen sich EINE IP und damit ein Anfrage-Budget (Abstand ≥ spacing_s).
    Steigt der Abstand nach 429/403, werden auch die Intervalle automatisch länger.
    """
    budget = self.filters.active_count() * self.limiter.spacing_s * CATALOG_BUDGET_FACTOR
    return max(float(flt.interval_s), budget)

  # --- Interna ---------------------------------------------------------------

  def _start_task(self, coro, name: str) -> None:  # noqa: ANN001 – Coroutine
    self._tasks.append(asyncio.create_task(coro, name=name))

  async def _after_ready(self, coro) -> None:  # noqa: ANN001 – Coroutine
    # Erst senden, wenn die Gateway-Verbindung steht (Kanal-Cache gefüllt)
    await self.wait_until_ready()
    await coro

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
    log.info("Start-Filter %s angelegt (%s)", filter_id, parsed.canonical_url)

  async def _send_entry(self, entry: OutboxEntry) -> discord.Message:
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
      message = await channel.send(embeds=embeds, view=view)
    except (discord.Forbidden, discord.NotFound) as error:
      raise PermanentSendError(f"Kanal {entry.channel_id} nicht nutzbar: {error}") from error
    log.info("Gepostet: %s (Filter %s)", entry.item.title, entry.filter_name)
    return message

  async def _edit_message(self, message: discord.Message, entry: OutboxEntry) -> None:
    """Vom Enricher aufgerufen: Post mit Galerie/Beschreibung/Flagge aktualisieren."""
    embeds, view = build_message(entry)
    try:
      await message.edit(embeds=embeds, view=view)
    except discord.NotFound:
      log.info("Nachricht zu Artikel %s wurde inzwischen gelöscht", entry.item_id)

  async def _on_channel_unusable(self, entry: OutboxEntry, reason: str) -> None:
    """Kanal gelöscht oder keine Rechte: Filter pausieren, statt Discord weiter zu bedrängen."""
    flt = await self.repo.get_filter(entry.filter_id)
    if flt is None or flt.paused:
      return
    await self.repo.set_paused(flt.id, True)
    await self.filters.stop(flt.id)
    self.notifier.post(
      StatusEvent(
        Severity.ERROR,
        f"Filter #{flt.id} {flt.name} pausiert",
        f"Sein Kanal ist nicht mehr nutzbar: {quote_error(reason)}\n"
        "Kanal wiederherstellen und `/filter resume`, oder `/filter remove` und neu anlegen.",
        key=f"channel:{flt.id}",
      )
    )

  async def _deliver_status(self, event: StatusEvent) -> None:
    guild = self.get_guild(self.settings.guild_id)
    if guild is None:
      log.warning("Server %s nicht gefunden – Statusmeldung nur im Log", self.settings.guild_id)
      return
    channel = await resolve_admin_channel(guild, self.repo, self.settings.admin_channel_id)
    await channel.send(embed=status_embed(event))

  async def _collect_status(self) -> StatusData:
    filters = await self.repo.list_filters()
    domains = {f.domain for f in filters} | set(self.guard.known_domains())
    admin_id = self.settings.admin_channel_id or await self.repo.get_value("admin_channel_id")
    return StatusData(
      started_at=self.started_at,
      filters=filters,
      blocks={domain: self.guard.current(domain) for domain in domains},
      stats=self.vinted.stats.summary(),
      spacing_s=self.limiter.spacing_s,
      intervals={f.id: self.effective_interval(f) for f in filters if not f.paused},
      pending=await self.repo.count_pending(),
      posted_24h=await self.repo.count_posted_since(utc_now() - timedelta(hours=24)),
      admin_channel_id=int(admin_id) if admin_id else None,
    )

  async def _housekeeping(self) -> None:
    """Alle 6 h: alte gesehene Artikel (> 30 Tage) und Verkäufer (> 60 Tage) löschen."""
    while True:
      try:
        removed = await self.repo.cleanup(
          items_older_than=KEEP_ITEMS, sellers_older_than=KEEP_SELLERS
        )
        expired = await self.repo.expire_stale_outbox(OUTBOX_MAX_AGE)
        if removed or expired:
          log.info("Aufgeräumt: %s alte Einträge, %s veraltete Posts verworfen", removed, expired)
      except Exception:
        log.exception("Aufräumen fehlgeschlagen")
      await asyncio.sleep(HOUSEKEEPING_EVERY_S)
