"""Slash-Commands /filter … – nur Ein-/Ausgabe, die Logik steckt in core/filter_service.py.

Alle Antworten sind "ephemeral" (nur für dich sichtbar), damit die Filter-Kanäle sauber bleiben.
Nur OWNER_ID darf die Commands benutzen; zusätzlich sind sie standardmäßig nur für Admins
sichtbar (default_permissions).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from vinted_bot.core.filter_service import FilterService, UserError
from vinted_bot.discord_bot.channels import create_filter_channel
from vinted_bot.discord_bot.embeds import VINTED_TEAL
from vinted_bot.storage.repository import FilterRow, Repository
from vinted_bot.vinted.countries import flag_emoji

if TYPE_CHECKING:
  from vinted_bot.discord_bot.bot import VintedBot

log = logging.getLogger(__name__)


def describe_filter(flt: FilterRow, effective_s: float | None = None) -> str:
  """Eine Zeile pro Filter für /filter list und Bestätigungen."""
  countries = " ".join(flag_emoji(c) for c in flt.countries) if flt.countries else "🌍 alle"
  status = "⏸️ pausiert" if flt.paused else "▶️ aktiv"
  if not flt.paused and flt.fail_count:
    status = f"⚠️ {flt.fail_count} Fehler in Folge"
  last = _relative(flt.last_success_at) if flt.last_success_at else "noch nie"
  interval = f"alle {flt.interval_s} s"
  photos = "" if flt.item_details else " · 📷 1 Foto"
  if effective_s is not None and not flt.paused and round(effective_s) > flt.interval_s:
    interval += f" (effektiv ~{round(effective_s)} s)"
  return (
    f"**#{flt.id} {flt.name}** · <#{flt.channel_id}> · {status}\n"
    f"Länder: {countries} · {interval}{photos} · vinted.{flt.domain} · "
    f"letzte Abfrage: {last}"
  )


def _relative(moment: datetime) -> str:
  return f"<t:{int(moment.timestamp())}:R>"


class FilterCommands(app_commands.Group):
  def __init__(self, bot: VintedBot, service: FilterService, repo: Repository) -> None:
    super().__init__(
      name="filter",
      description="Vinted-Filter verwalten",
      guild_only=True,
      default_permissions=discord.Permissions(administrator=True),
    )
    self.bot = bot
    self.service = service
    self.repo = repo

  async def interaction_check(self, interaction: discord.Interaction) -> bool:
    if interaction.user.id != self.bot.settings.owner_id:
      await interaction.response.send_message("Nur der Bot-Besitzer darf das.", ephemeral=True)
      return False
    return True

  async def on_error(
    self, interaction: discord.Interaction, error: app_commands.AppCommandError
  ) -> None:
    original = getattr(error, "original", error)
    if isinstance(original, UserError):
      message = f"❌ {original}"
    else:
      log.exception("Fehler in Slash-Command", exc_info=original)
      message = f"❌ Unerwarteter Fehler: {type(original).__name__}. Details im Log."
    await _reply(interaction, message)

  # --- /filter add -------------------------------------------------------------

  @app_commands.command(name="add", description="Neuen Vinted-Filter anlegen")
  @app_commands.describe(
    link="Vinted-Link aus der Adresszeile (vinted.at oder vinted.de)",
    name="Anzeigename, wird auch Kanalname (z. B. ralph-lauren-jacken)",
    laender="Nur Verkäufer aus diesen Ländern, z. B. at,de,it,fr – leer = alle",
    intervall="Abfrageintervall in Sekunden (10–3600, Standard 10)",
    kanal="Vorhandenen Kanal verwenden statt einen neuen anzulegen",
    galerie="Bis zu 3 Fotos + Beschreibung nachladen (Standard: ja; nein spart Anfragen)",
  )
  async def add(
    self,
    interaction: discord.Interaction,
    link: str,
    name: str | None = None,
    laender: str | None = None,
    intervall: app_commands.Range[int, 10, 3600] | None = None,
    kanal: discord.TextChannel | None = None,
    galerie: bool = True,
  ) -> None:
    # Kanal anlegen kann länger als 3 s dauern -> Antwort ankündigen
    await interaction.response.defer(ephemeral=True, thinking=True)
    prepared = self.service.prepare(link, laender, intervall)
    filter_name = (name or "").strip() or await self._default_name()

    if kanal is not None:
      if await self.service.is_duplicate(prepared, kanal.id):
        raise UserError("Diesen Filter gibt es in diesem Kanal schon.")
      channel = kanal
    else:
      assert interaction.guild is not None  # guild_only
      topic = f"Vinted-Filter: {prepared.parsed.canonical_url}"
      channel = await create_filter_channel(interaction.guild, self.repo, filter_name, topic)

    flt = await self.service.create(prepared, filter_name, channel.id, item_details=galerie)
    lines = [
      "✅ Filter angelegt:",
      describe_filter(flt),
      "Beim ersten Abruf wird nur der aktuelle Stand gemerkt – gepostet wird ab dem nächsten "
      "neuen Artikel.",
    ]
    if prepared.parsed.ignored:
      lines.append(
        f"⚠️ Nicht unterstützte Link-Teile ignoriert: `{', '.join(prepared.parsed.ignored)}`"
      )
    await _reply(interaction, "\n".join(lines))

  # --- /filter list ------------------------------------------------------------

  @app_commands.command(name="list", description="Alle Filter anzeigen")
  async def list_filters(self, interaction: discord.Interaction) -> None:
    filters = await self.service.list()
    if not filters:
      await _reply(interaction, "Noch keine Filter. Lege einen mit `/filter add` an.")
      return
    embed = discord.Embed(title="Vinted-Filter", color=VINTED_TEAL)
    embed.description = "\n\n".join(
      describe_filter(f, self.bot.effective_interval(f) if not f.paused else None) for f in filters
    )[:4096]
    embed.set_footer(
      text="Tipp: Unwichtige Filter auf 60–120 s stellen (/filter intervall) – "
      "dann werden die wichtigen schneller."
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

  # --- /filter pause | resume | remove -------------------------------------------

  @app_commands.command(name="pause", description="Filter anhalten")
  @app_commands.describe(filter="Welcher Filter?")
  async def pause(self, interaction: discord.Interaction, filter: int) -> None:  # noqa: A002
    flt = await self.service.pause(filter)
    await _reply(interaction, f"⏸️ Pausiert: **#{flt.id} {flt.name}**")

  @app_commands.command(name="resume", description="Pausierten Filter fortsetzen")
  @app_commands.describe(filter="Welcher Filter?")
  async def resume(self, interaction: discord.Interaction, filter: int) -> None:  # noqa: A002
    flt = await self.service.resume(filter)
    await _reply(interaction, f"▶️ Läuft wieder: **#{flt.id} {flt.name}**")

  @app_commands.command(name="remove", description="Filter löschen")
  @app_commands.describe(
    filter="Welcher Filter?",
    kanal_loeschen="Auch den Kanal löschen (nur wenn kein anderer Filter hineinpostet)",
  )
  async def remove(
    self,
    interaction: discord.Interaction,
    filter: int,  # noqa: A002
    kanal_loeschen: bool = False,
  ) -> None:
    flt, channel_unused = await self.service.remove(filter)
    message = f"🗑️ Gelöscht: **#{flt.id} {flt.name}**"
    if kanal_loeschen and channel_unused:
      channel = self.bot.get_channel(flt.channel_id)
      if isinstance(channel, discord.TextChannel):
        await channel.delete(reason=f"Vinted-Bot: Filter #{flt.id} gelöscht")
        message += " samt Kanal"
    elif kanal_loeschen:
      message += " (Kanal bleibt, weil noch andere Filter hineinposten)"
    await _reply(interaction, message)

  # --- /filter laender | intervall ---------------------------------------------

  @app_commands.command(name="laender", description="Länderfilter ändern")
  @app_commands.describe(filter="Welcher Filter?", kuerzel="z. B. at,de,it,fr – leer = alle Länder")
  async def countries(
    self,
    interaction: discord.Interaction,
    filter: int,  # noqa: A002
    kuerzel: str | None = None,
  ) -> None:
    flt = await self.service.set_countries(filter, kuerzel)
    await _reply(interaction, f"🌍 Geändert:\n{describe_filter(flt)}")

  @app_commands.command(name="intervall", description="Abfrageintervall ändern")
  @app_commands.describe(filter="Welcher Filter?", sekunden="10–3600")
  async def interval(
    self,
    interaction: discord.Interaction,
    filter: int,  # noqa: A002
    sekunden: app_commands.Range[int, 10, 3600],
  ) -> None:
    flt = await self.service.set_interval(filter, sekunden)
    await _reply(
      interaction, f"⏱️ Geändert:\n{describe_filter(flt, self.bot.effective_interval(flt))}"
    )

  @app_commands.command(name="galerie", description="Fotos 2–3 + Beschreibung an/aus")
  @app_commands.describe(
    filter="Welcher Filter?",
    an="An: bis zu 3 Fotos + Beschreibung. Aus: nur 1 Foto (spart Anfragen an Vinted)",
  )
  async def gallery(
    self,
    interaction: discord.Interaction,
    filter: int,  # noqa: A002
    an: bool,
  ) -> None:
    flt = await self.service.set_item_details(filter, an)
    await _reply(interaction, f"📷 Geändert:\n{describe_filter(flt)}")

  # --- Autovervollständigung für den Parameter "filter" --------------------------

  @pause.autocomplete("filter")
  @resume.autocomplete("filter")
  @remove.autocomplete("filter")
  @countries.autocomplete("filter")
  @interval.autocomplete("filter")
  @gallery.autocomplete("filter")
  async def filter_autocomplete(
    self, interaction: discord.Interaction, current: str
  ) -> list[app_commands.Choice[int]]:
    # Autovervollständigung läuft NICHT durch interaction_check -> selbst prüfen
    if interaction.user.id != self.bot.settings.owner_id:
      return []
    filters = await self.service.list()
    current = current.lower()
    choices = [
      app_commands.Choice(name=f"#{f.id} {f.name}"[:100], value=f.id)
      for f in filters
      if current in f"#{f.id} {f.name}".lower()
    ]
    return choices[:25]  # Discord-Limit

  async def _default_name(self) -> str:
    return f"filter-{len(await self.service.list()) + 1}"


async def _reply(interaction: discord.Interaction, message: str) -> None:
  """Antwortet passend, egal ob schon geantwortet/"defer"t wurde."""
  if interaction.response.is_done():
    await interaction.followup.send(message, ephemeral=True)
  else:
    await interaction.response.send_message(message, ephemeral=True)
