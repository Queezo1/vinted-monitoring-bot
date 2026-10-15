"""Migration einer alten Datenbank (Schema 1, Mindestintervall 30 s) auf Schema 2."""

import sqlite3
from importlib import resources
from pathlib import Path

import pytest

from vinted_bot.storage.db import SCHEMA_VERSION, open_database


def create_v1_database(path: Path) -> None:
  schema = resources.files("vinted_bot.storage").joinpath("schema.sql").read_text("utf-8")
  v1_schema = schema.replace(
    "DEFAULT 10 CHECK (interval_s >= 10)", "DEFAULT 60 CHECK (interval_s >= 30)"
  )
  assert v1_schema != schema
  conn = sqlite3.connect(path)
  conn.executescript(v1_schema)
  conn.execute("INSERT INTO schema_version VALUES (1)")
  conn.execute(
    """INSERT INTO filters (id, name, source_url, canonical_url, domain, api_params_json,
                            channel_id, countries, interval_s, baseline_done, created_at)
       VALUES (1, 'rl', 'x', 'x', 'at', '{}', 5, 'AT', 60, 1, '2026-10-15T00:00:00+00:00')"""
  )
  conn.execute(
    "INSERT INTO seen_items VALUES (1, 111, '2026-10-15T00:00:00+00:00', 'posted', NULL, NULL, 0)"
  )
  conn.commit()
  conn.close()


async def test_v1_database_is_migrated_without_data_loss(tmp_path: Path) -> None:
  path = tmp_path / "bot.db"
  create_v1_database(path)

  conn = await open_database(path)
  async with conn.execute("SELECT version FROM schema_version") as cursor:
    assert (await cursor.fetchone())["version"] == SCHEMA_VERSION
  async with conn.execute("SELECT name, countries, interval_s FROM filters") as cursor:
    assert tuple(await cursor.fetchone()) == ("rl", "AT", 60)
  async with conn.execute("SELECT item_id FROM seen_items") as cursor:
    assert [r["item_id"] for r in await cursor.fetchall()] == [111]  # nicht weg-kaskadiert

  # 10 s ist jetzt erlaubt, 9 s nicht
  await conn.execute("UPDATE filters SET interval_s = 10")
  with pytest.raises(sqlite3.IntegrityError):
    await conn.execute("UPDATE filters SET interval_s = 9")
  # Fremdschlüssel wieder aktiv: Löschen kaskadiert
  await conn.execute("DELETE FROM filters")
  async with conn.execute("SELECT COUNT(*) AS n FROM seen_items") as cursor:
    assert (await cursor.fetchone())["n"] == 0
  await conn.close()


async def test_migration_is_idempotent(tmp_path: Path) -> None:
  path = tmp_path / "bot.db"
  create_v1_database(path)
  for _ in range(2):  # erster Start migriert, zweiter hat nichts mehr zu tun
    conn = await open_database(path)
    await conn.close()
  conn = await open_database(path)
  async with conn.execute("SELECT COUNT(*) AS n FROM filters") as cursor:
    assert (await cursor.fetchone())["n"] == 1
  await conn.close()
