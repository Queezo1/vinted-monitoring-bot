"""SQLite-Verbindung öffnen und Schema anlegen."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import aiosqlite

SCHEMA_VERSION = 1


async def open_database(path: Path | str) -> aiosqlite.Connection:
  """Öffnet (und erzeugt bei Bedarf) die Datenbank. ':memory:' geht für Tests."""
  if str(path) != ":memory:":
    Path(path).parent.mkdir(parents=True, exist_ok=True)
  conn = await aiosqlite.connect(path)
  conn.row_factory = aiosqlite.Row  # Zeilen per Spaltenname lesbar: row["name"]
  # WAL: Lesen blockiert Schreiben nicht; robuster bei Stromausfall
  await conn.execute("PRAGMA journal_mode = WAL")
  await conn.execute("PRAGMA foreign_keys = ON")
  await _migrate(conn)
  return conn


async def _migrate(conn: aiosqlite.Connection) -> None:
  schema = resources.files("vinted_bot.storage").joinpath("schema.sql").read_text("utf-8")
  await conn.executescript(schema)
  async with conn.execute("SELECT version FROM schema_version") as cursor:
    row = await cursor.fetchone()
  if row is None:
    await conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
  elif row["version"] > SCHEMA_VERSION:
    raise RuntimeError(
      f"Datenbank hat Schema {row['version']}, dieser Code kennt nur {SCHEMA_VERSION}"
    )
  await conn.commit()
