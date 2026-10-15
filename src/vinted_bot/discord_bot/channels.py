"""Kategorie "Vinted" und Kanäle pro Filter anlegen."""

from __future__ import annotations

import re
import unicodedata

import discord

from vinted_bot.storage.repository import Repository

CATEGORY_NAME = "Vinted"
CATEGORY_KEY = "discord_category_id"
MAX_CHANNELS_PER_CATEGORY = 50  # Discord-Limit


def channel_slug(name: str) -> str:
  """'Ralph Lauren Jacken (AT)' -> 'ralph-lauren-jacken-at' (Discord: max. 100 Zeichen)."""
  # Umlaute lesbar umschreiben, bevor alles Nicht-ASCII entfernt wird
  text = name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
  text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
  text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
  return text[:100] or "vinted-filter"


async def get_or_create_category(guild: discord.Guild, repo: Repository) -> discord.CategoryChannel:
  """Kategorie wiederverwenden (per gespeicherter ID oder Name), sonst neu anlegen."""
  stored = await repo.get_value(CATEGORY_KEY)
  if stored is not None:
    category = guild.get_channel(int(stored))
    if isinstance(category, discord.CategoryChannel):
      return category
  category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
  if category is None:
    category = await guild.create_category(CATEGORY_NAME, reason="Vinted-Bot: Filter-Kanäle")
  await repo.set_value(CATEGORY_KEY, str(category.id))
  return category


async def create_filter_channel(
  guild: discord.Guild, repo: Repository, name: str, topic: str
) -> discord.TextChannel:
  category = await get_or_create_category(guild, repo)
  if len(category.channels) >= MAX_CHANNELS_PER_CATEGORY:
    raise discord.DiscordException(
      f"Die Kategorie '{category.name}' ist voll ({MAX_CHANNELS_PER_CATEGORY} Kanäle)."
    )
  return await guild.create_text_channel(
    channel_slug(name), category=category, topic=topic[:1024], reason="Vinted-Bot: neuer Filter"
  )
