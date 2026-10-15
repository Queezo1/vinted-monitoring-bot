"""/status – Überblick über Bot, Filter, Vinted-Erreichbarkeit und Last.

`build_status_embed` ist eine reine Funktion (alle Daten werden übergeben) und daher testbar.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

import discord
from discord import app_commands

from vinted_bot.core.domain_guard import DomainBlock
from vinted_bot.discord_bot.commands import describe_filter
from vinted_bot.discord_bot.embeds import VINTED_TEAL
from vinted_bot.storage.repository import FilterRow
from vinted_bot.vinted.stats import StatsSummary

_KIND_LABELS = {
  "catalog": "Katalog",
  "user": "Verkäufer",
  "item_page": "Artikelseiten",
  "session": "Sessions",
}


@dataclass(frozen=True)
class StatusData:
  started_at: datetime
  filters: list[FilterRow]
  blocks: dict[str, DomainBlock | None]  # Domain -> aktive Sperre oder None
  stats: StatsSummary
  pending: int
  posted_24h: int
  admin_channel_id: int | None


def build_status_embed(data: StatusData) -> discord.Embed:
  any_block = any(block is not None for block in data.blocks.values())
  embed = discord.Embed(
    title="⛔ Vinted-Bot: eingeschränkt" if any_block else "✅ Vinted-Bot läuft",
    color=0xE74C3C if any_block else VINTED_TEAL,
  )
  active = sum(1 for f in data.filters if not f.paused)
  embed.add_field(name="Gestartet", value=f"<t:{int(data.started_at.timestamp())}:R>")
  embed.add_field(name="Filter", value=f"{active} aktiv · {len(data.filters) - active} pausiert")
  embed.add_field(name="Gepostet (24 h)", value=f"{data.posted_24h} · {data.pending} wartend")

  embed.add_field(name="Vinted", value=_domains_text(data.blocks), inline=False)
  embed.add_field(name="Anfragen (letzte Stunde)", value=_stats_text(data.stats), inline=False)

  if data.filters:
    text = "\n\n".join(describe_filter(f) for f in data.filters)
    embed.add_field(name="Filter", value=_fit(text, 1024), inline=False)
  if data.admin_channel_id:
    embed.set_footer(text="Statusmeldungen landen im Admin-Kanal #vinted-status")
  return embed


def _domains_text(blocks: dict[str, DomainBlock | None]) -> str:
  if not blocks:
    return "keine Filter"
  lines = []
  for domain, block in sorted(blocks.items()):
    if block is None:
      lines.append(f"✅ vinted.{domain}: erreichbar")
    else:
      reason = "403 abgewiesen" if block.reason == "blocked" else "429 zu viele Anfragen"
      lines.append(
        f"⛔ vinted.{domain}: {reason} (Stufe {block.level}), "
        f"nächster Versuch <t:{int(block.until.timestamp())}:R>"
      )
  return "\n".join(lines)


def _stats_text(stats: StatsSummary) -> str:
  if stats.total == 0:
    return "keine"
  kinds = " · ".join(
    f"{_KIND_LABELS.get(kind, kind)} {count}" for kind, count in sorted(stats.by_kind.items())
  )
  errors = {k: v for k, v in stats.by_outcome.items() if k != "200"}
  error_text = ", ".join(f"{k}: {v}" for k, v in sorted(errors.items())) if errors else "keine"
  return f"{stats.total} gesamt ({kinds})\nFehler: {error_text}"


def _fit(text: str, limit: int) -> str:
  """Feldwerte dürfen max. 1024 Zeichen haben – sonst kürzen mit Hinweis."""
  if len(text) <= limit:
    return text
  hint = "\n… (vollständig: /filter list)"
  return text[: limit - len(hint)] + hint


def make_status_command(
  collect: Callable[[], Awaitable[StatusData]], owner_id: int
) -> app_commands.Command:
  """Baut den /status-Command. `collect` liefert die aktuellen Daten (vom Bot bereitgestellt)."""

  @app_commands.command(name="status", description="Zustand des Vinted-Bots anzeigen")
  @app_commands.guild_only()
  @app_commands.default_permissions(administrator=True)
  async def status(interaction: discord.Interaction) -> None:
    if interaction.user.id != owner_id:
      await interaction.response.send_message("Nur der Bot-Besitzer darf das.", ephemeral=True)
      return
    embed = build_status_embed(await collect())
    await interaction.response.send_message(embed=embed, ephemeral=True)

  return status
