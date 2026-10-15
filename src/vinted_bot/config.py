"""Konfiguration aus Umgebungsvariablen bzw. der .env-Datei laden und prüfen.

`parse_settings()` ist eine reine Funktion (Mapping rein, Settings raus), damit sie
ohne echte .env-Datei testbar ist. `load_settings()` liest zusätzlich die .env ein.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from vinted_bot import __version__
from vinted_bot.vinted.countries import InvalidCountryCodeError, parse_country_codes
from vinted_bot.vinted.url_parser import InvalidFilterUrlError, parse_filter_url

# Untergrenze pro Filter (vinted-sniper nutzt dieselbe). Darunter steigt das Sperrrisiko
# stark, während der Gewinn klein ist (Vinted zeigt neue Artikel ohnehin verzögert an).
MIN_INTERVAL_S = 10
MIN_SPACING_S = 2.0

# Ehrlicher User-Agent: sagt, was wir sind. Ob Vinted ihn akzeptiert, klärt die M0-Probe.
HONEST_USER_AGENT = f"VintedPrivateMonitor/{__version__} (private, non-commercial, low rate)"

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


class ConfigError(Exception):
  """Die Konfiguration ist unvollständig oder ungültig. Enthält alle gefundenen Probleme."""

  def __init__(self, problems: list[str]) -> None:
    self.problems = problems
    super().__init__("Ungültige Konfiguration:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass(frozen=True)
class Settings:
  # repr=False: das Token darf nie in Logs oder Fehlermeldungen landen
  discord_token: str = field(repr=False)
  guild_id: int
  owner_id: int
  admin_channel_id: int | None
  vinted_user_agent: str
  default_interval_s: int
  global_min_spacing_s: float
  db_path: Path
  log_dir: Path
  log_level: str
  # Verkäufer nachschlagen (Flagge, Sterne, Avatar). Bei gesetztem Länderfilter immer an.
  seller_lookup: bool = True
  # Artikelseite laden für alle Fotos + Beschreibung (1 Request pro geposteten Artikel)
  item_details: bool = True
  # M2: ein Start-Filter aus der .env (ab M3 per Slash-Command)
  seed_filter_url: str | None = None
  seed_filter_name: str = "start-filter"
  seed_filter_countries: tuple[str, ...] = ()
  seed_channel_id: int | None = None


def parse_settings(env: Mapping[str, str]) -> Settings:
  """Baut Settings aus einem Mapping. Sammelt alle Fehler, statt beim ersten abzubrechen."""
  problems: list[str] = []

  def get(name: str) -> str:
    return env.get(name, "").strip()

  def required_str(name: str) -> str:
    value = get(name)
    if not value:
      problems.append(f"{name} fehlt")
    return value

  def int_value(name: str, *, required: bool, default: int | None = None) -> int | None:
    raw = get(name)
    if not raw:
      if required:
        problems.append(f"{name} fehlt")
      return default
    try:
      return int(raw)
    except ValueError:
      problems.append(f"{name} muss eine ganze Zahl sein (ist: {raw!r})")
      return default

  token = required_str("DISCORD_TOKEN")
  guild_id = int_value("GUILD_ID", required=True)
  owner_id = int_value("OWNER_ID", required=True)
  admin_channel_id = int_value("ADMIN_CHANNEL_ID", required=False)

  interval = int_value("DEFAULT_INTERVAL_S", required=False, default=10)
  if interval is not None and interval < MIN_INTERVAL_S:
    problems.append(f"DEFAULT_INTERVAL_S muss >= {MIN_INTERVAL_S} sein (ist: {interval})")

  spacing_raw = get("GLOBAL_MIN_SPACING_S") or "3"
  spacing = 3.0
  try:
    spacing = float(spacing_raw)
    if spacing < MIN_SPACING_S:
      problems.append(f"GLOBAL_MIN_SPACING_S muss >= {MIN_SPACING_S:g} sein (ist: {spacing})")
  except ValueError:
    problems.append(f"GLOBAL_MIN_SPACING_S muss eine Zahl sein (ist: {spacing_raw!r})")

  log_level = (get("LOG_LEVEL") or "INFO").upper()
  if log_level not in _LOG_LEVELS:
    problems.append(f"LOG_LEVEL muss einer von {sorted(_LOG_LEVELS)} sein (ist: {log_level!r})")

  def bool_value(name: str, default: bool) -> bool:
    raw = (get(name) or str(default)).lower()
    if raw not in ("true", "false"):
      problems.append(f"{name} muss true oder false sein (ist: {raw!r})")
    return raw == "true"

  seller_lookup = bool_value("SELLER_LOOKUP", True)
  item_details = bool_value("ITEM_DETAILS", True)

  seed_url = get("SEED_FILTER_URL") or None
  seed_channel_id = int_value("SEED_CHANNEL_ID", required=False)
  seed_countries: tuple[str, ...] = ()
  if seed_url:
    try:
      parse_filter_url(seed_url)
    except InvalidFilterUrlError as error:
      problems.append(f"SEED_FILTER_URL: {error}")
    if seed_channel_id is None:
      problems.append("SEED_CHANNEL_ID fehlt (nötig, wenn SEED_FILTER_URL gesetzt ist)")
  try:
    seed_countries = parse_country_codes(get("SEED_FILTER_COUNTRIES"))
  except InvalidCountryCodeError as error:
    problems.append(f"SEED_FILTER_COUNTRIES: {error}")

  if problems:
    raise ConfigError(problems)

  # Nach der Prüfung oben sind die Pflichtwerte garantiert gesetzt.
  assert guild_id is not None and owner_id is not None and interval is not None
  return Settings(
    discord_token=token,
    guild_id=guild_id,
    owner_id=owner_id,
    admin_channel_id=admin_channel_id,
    vinted_user_agent=get("VINTED_USER_AGENT") or HONEST_USER_AGENT,
    default_interval_s=interval,
    global_min_spacing_s=spacing,
    db_path=Path(get("DB_PATH") or "data/bot.db"),
    log_dir=Path(get("LOG_DIR") or "logs"),
    log_level=log_level,
    seller_lookup=seller_lookup,
    item_details=item_details,
    seed_filter_url=seed_url,
    seed_filter_name=get("SEED_FILTER_NAME") or "start-filter",
    seed_filter_countries=seed_countries,
    seed_channel_id=seed_channel_id,
  )


def load_settings(env_file: Path | None = Path(".env")) -> Settings:
  """Liest die .env (falls vorhanden) in os.environ und baut daraus Settings.

  Bereits gesetzte Umgebungsvariablen haben Vorrang vor der .env (override=False).
  """
  if env_file is not None and env_file.exists():
    load_dotenv(env_file, override=False)
  return parse_settings(os.environ)
