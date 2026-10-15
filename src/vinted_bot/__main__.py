"""Einstiegspunkt: `python -m vinted_bot`."""

from __future__ import annotations

import asyncio
import logging
import sys

import discord

from vinted_bot import __version__
from vinted_bot.config import ConfigError, Settings, load_settings
from vinted_bot.discord_bot.bot import VintedBot
from vinted_bot.logging_setup import setup_logging

log = logging.getLogger("vinted_bot")


async def run(settings: Settings) -> None:
  bot = VintedBot(settings)
  # "async with" ruft beim Verlassen bot.close() auf – auch bei Strg+C
  async with bot:
    await bot.start(settings.discord_token)


def main() -> int:
  try:
    settings = load_settings()
  except ConfigError as error:
    # Logging ist hier noch nicht eingerichtet, daher direkt auf stderr
    print(error, file=sys.stderr)
    print("Tipp: .env.example nach .env kopieren und ausfüllen.", file=sys.stderr)
    return 2

  setup_logging(settings.log_level, settings.log_dir)
  log.info("vinted-bot %s – Konfiguration ok", __version__)
  log.info(
    "Intervall=%ss, globaler Abstand=%ss, Verkäufer-Lookup=%s, DB=%s",
    settings.default_interval_s,
    settings.global_min_spacing_s,
    settings.seller_lookup,
    settings.db_path,
  )

  try:
    asyncio.run(run(settings))
  except KeyboardInterrupt:
    log.info("Beendet (Strg+C)")
  except discord.LoginFailure:
    log.error("Discord-Login fehlgeschlagen: DISCORD_TOKEN prüfen")
    return 1
  return 0


if __name__ == "__main__":
  sys.exit(main())
