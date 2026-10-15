"""SQLite-Verbindung öffnen, Schema anlegen und ältere Datenbanken migrieren."""

from __future__ import annotations

import logging
import re
from importlib import resources
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3


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
    await conn.commit()
    return
  version = row["version"]
  if version > SCHEMA_VERSION:
    raise RuntimeError(f"Datenbank hat Schema {version}, dieser Code kennt nur {SCHEMA_VERSION}")
  if version < 2:
    await _migrate_to_v2(conn)
  if version < 3:
    await _migrate_to_v3(conn)
  await conn.commit()


async def _migrate_to_v2(conn: aiosqlite.Connection) -> None:
  """v1 -> v2: Mindestintervall 10 s statt 30 s.

  SQLite kann CHECK-Bedingungen nicht ändern, deshalb wird die Tabelle nach dem offiziellen
  Verfahren neu aufgebaut (https://sqlite.org/lang_altertable.html, "12 Schritte").
  Fremdschlüssel werden dabei kurz abgeschaltet – sonst würde DROP TABLE per ON DELETE CASCADE
  alle gesehenen Artikel mitlöschen.
  """
  async with conn.execute(
    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'filters'"
  ) as cursor:
    row = await cursor.fetchone()
  old_sql: str = row["sql"]
  new_sql = re.sub(
    r"CREATE TABLE (IF NOT EXISTS )?\"?filters\"?", "CREATE TABLE filters_new", old_sql
  )
  new_sql = new_sql.replace("CHECK (interval_s >= 30)", "CHECK (interval_s >= 10)")

  await conn.commit()
  await conn.execute("PRAGMA foreign_keys = OFF")
  try:
    await conn.executescript(
      f"""
      BEGIN;
      {new_sql};
      INSERT INTO filters_new SELECT * FROM filters;
      DROP TABLE filters;
      ALTER TABLE filters_new RENAME TO filters;
      UPDATE schema_version SET version = 2;
      COMMIT;
      """
    )
    async with conn.execute("PRAGMA foreign_key_check") as cursor:
      if await cursor.fetchall():
        raise RuntimeError("Migration v2: Fremdschlüssel inkonsistent")
  finally:
    await conn.execute("PRAGMA foreign_keys = ON")
  log.info("Datenbank auf Schema 2 migriert (Mindestintervall 10 s)")


async def _migrate_to_v3(conn: aiosqlite.Connection) -> None:
  """v2 -> v3: Spalte item_details (Galerie/Beschreibung pro Filter abschaltbar)."""
  async with conn.execute("PRAGMA table_info(filters)") as cursor:
    columns = {row["name"] for row in await cursor.fetchall()}
  if "item_details" not in columns:  # bei neuen DBs legt schema.sql sie schon an
    await conn.execute("ALTER TABLE filters ADD COLUMN item_details INTEGER NOT NULL DEFAULT 1")
  await conn.execute("UPDATE schema_version SET version = 3")
  await conn.commit()
  log.info("Datenbank auf Schema 3 migriert (Galerie pro Filter)")
