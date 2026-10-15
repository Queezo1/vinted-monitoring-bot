"""Einstiegspunkt: `python -m vinted_bot` (mit Konsole) bzw. `pythonw -m vinted_bot` (Autostart).

Für den Autostart beim Windows-Login wichtig:
  - Nur eine Instanz: Läuft der Bot schon, beendet sich der zweite Start sofort.
  - Netzwerk noch nicht bereit (direkt nach dem Login üblich): warten und neu versuchen,
    statt abzubrechen. Dasselbe gilt, falls die Verbindung später endgültig abreißt.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

import discord

from vinted_bot import __version__
from vinted_bot.config import ConfigError, Settings, load_settings
from vinted_bot.discord_bot.bot import VintedBot
from vinted_bot.logging_setup import setup_logging
from vinted_bot.single_instance import AlreadyRunningError, InstanceLock

log = logging.getLogger("vinted_bot")

RETRY_START_S = 30
RETRY_MAX_S = 300
# Lief der Bot mindestens so lange, gilt der nächste Fehler wieder als "erster"
STABLE_AFTER_S = 600


async def run(settings: Settings) -> None:
  bot = VintedBot(settings)
  # "async with" ruft beim Verlassen bot.close() auf – auch bei Strg+C
  async with bot:
    await bot.start(settings.discord_token)


def run_with_retries(settings: Settings) -> int:
  delay_s = RETRY_START_S
  while True:
    started = time.monotonic()
    try:
      asyncio.run(run(settings))
      return 0
    except KeyboardInterrupt:
      log.info("Beendet (Strg+C)")
      return 0
    except discord.LoginFailure:
      log.error("Discord-Login fehlgeschlagen: DISCORD_TOKEN prüfen")
      return 1
    except OSError as error:
      # Netzwerk weg/noch nicht da (aiohttp-Verbindungsfehler sind OSError-Unterklassen)
      if time.monotonic() - started >= STABLE_AFTER_S:
        delay_s = RETRY_START_S
      log.warning("Keine Verbindung (%s) – neuer Versuch in %s s", error, delay_s)
      time.sleep(delay_s)
      delay_s = min(RETRY_MAX_S, delay_s * 2)


def main() -> int:
  try:
    settings = load_settings()
  except ConfigError as error:
    # Logging ist hier noch nicht eingerichtet, daher direkt auf stderr (falls vorhanden)
    if sys.stderr:
      print(error, file=sys.stderr)
      print("Tipp: .env.example nach .env kopieren und ausfüllen.", file=sys.stderr)
    return 2

  setup_logging(settings.log_level, settings.log_dir)
  lock = InstanceLock(settings.db_path.parent / "bot.lock")
  try:
    lock.acquire()
  except AlreadyRunningError as error:
    log.info("%s Dieser Start wird beendet.", error)
    return 0

  try:
    log.info("vinted-bot %s – Konfiguration ok", __version__)
    log.info(
      "Intervall=%ss, globaler Abstand=%ss, Verkäufer-Lookup=%s, Artikelseiten=%s, DB=%s",
      settings.default_interval_s,
      settings.global_min_spacing_s,
      settings.seller_lookup,
      settings.item_details,
      settings.db_path,
    )
    return run_with_retries(settings)
  finally:
    lock.release()


if __name__ == "__main__":
  sys.exit(main())
