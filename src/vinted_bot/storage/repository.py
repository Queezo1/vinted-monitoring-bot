"""ALLE SQL-Statements an einem Ort. Der Rest des Programms kennt kein SQL.

Zeitstempel werden als ISO-8601-Text in UTC gespeichert (sortierbar, lesbar).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite

from vinted_bot.vinted.models import SellerInfo, VintedItem


def utc_now() -> datetime:
  return datetime.now(UTC)


@dataclass(frozen=True)
class FilterRow:
  id: int
  name: str
  source_url: str
  canonical_url: str
  domain: str
  api_params: dict[str, str]
  channel_id: int
  countries: tuple[str, ...]  # leer = alle Länder
  interval_s: int
  paused: bool
  baseline_done: bool
  max_seen_id: int | None
  # Status für /filter list
  last_success_at: datetime | None = None
  last_error: str | None = None
  fail_count: int = 0


@dataclass(frozen=True)
class OutboxEntry:
  filter_id: int
  item_id: int
  channel_id: int
  filter_name: str
  domain: str
  item: VintedItem
  seller: SellerInfo | None
  first_seen_at: datetime


@dataclass(frozen=True)
class NewItem:
  """Ein neu gefundener Artikel, wie ihn der Poller in die Outbox legt."""

  item: VintedItem
  seller: SellerInfo | None


class Repository:
  def __init__(self, conn: aiosqlite.Connection, now: Callable[[], datetime] = utc_now) -> None:
    self._conn = conn
    self._now = now

  # --- Filter ----------------------------------------------------------------

  async def add_filter(
    self,
    *,
    name: str,
    source_url: str,
    canonical_url: str,
    domain: str,
    api_params: dict[str, str],
    channel_id: int,
    countries: tuple[str, ...],
    interval_s: int,
  ) -> int:
    cursor = await self._conn.execute(
      """INSERT INTO filters (name, source_url, canonical_url, domain, api_params_json,
                              channel_id, countries, interval_s, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
      (
        name,
        source_url,
        canonical_url,
        domain,
        json.dumps(api_params, ensure_ascii=False),
        channel_id,
        ",".join(countries),
        interval_s,
        self._iso_now(),
      ),
    )
    await self._conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid

  async def find_filter(
    self, canonical_url: str, countries: tuple[str, ...], channel_id: int
  ) -> FilterRow | None:
    async with self._conn.execute(
      "SELECT * FROM filters WHERE canonical_url = ? AND countries = ? AND channel_id = ?",
      (canonical_url, ",".join(countries), channel_id),
    ) as cursor:
      row = await cursor.fetchone()
    return _filter_from_row(row) if row else None

  async def get_filter(self, filter_id: int) -> FilterRow | None:
    async with self._conn.execute("SELECT * FROM filters WHERE id = ?", (filter_id,)) as cursor:
      row = await cursor.fetchone()
    return _filter_from_row(row) if row else None

  async def list_filters(self, *, only_active: bool = False) -> list[FilterRow]:
    sql = "SELECT * FROM filters"
    if only_active:
      sql += " WHERE paused = 0"
    async with self._conn.execute(sql + " ORDER BY id") as cursor:
      rows = await cursor.fetchall()
    return [_filter_from_row(row) for row in rows]

  async def filters_in_channel(self, channel_id: int) -> list[FilterRow]:
    async with self._conn.execute(
      "SELECT * FROM filters WHERE channel_id = ? ORDER BY id", (channel_id,)
    ) as cursor:
      rows = await cursor.fetchall()
    return [_filter_from_row(row) for row in rows]

  async def set_paused(self, filter_id: int, paused: bool) -> None:
    await self._update_filter(filter_id, "paused = ?", int(paused))

  async def set_countries(self, filter_id: int, countries: tuple[str, ...]) -> None:
    await self._update_filter(filter_id, "countries = ?", ",".join(countries))

  async def set_interval(self, filter_id: int, interval_s: int) -> None:
    await self._update_filter(filter_id, "interval_s = ?", interval_s)

  async def delete_filter(self, filter_id: int) -> None:
    """Löscht den Filter samt gesehener Artikel (ON DELETE CASCADE)."""
    await self._conn.execute("DELETE FROM filters WHERE id = ?", (filter_id,))
    await self._conn.commit()

  async def _update_filter(self, filter_id: int, assignment: str, value: object) -> None:
    # assignment kommt nur aus dieser Klasse (nie aus Nutzereingaben) -> kein SQL-Injection-Risiko
    await self._conn.execute(f"UPDATE filters SET {assignment} WHERE id = ?", (value, filter_id))
    await self._conn.commit()

  async def record_poll_error(self, filter_id: int, message: str) -> int:
    """Speichert den Fehler und gibt die Anzahl aufeinanderfolgender Fehler zurück."""
    await self._conn.execute(
      "UPDATE filters SET last_error = ?, fail_count = fail_count + 1 WHERE id = ?",
      (message[:500], filter_id),
    )
    await self._conn.commit()
    async with self._conn.execute(
      "SELECT fail_count FROM filters WHERE id = ?", (filter_id,)
    ) as cursor:
      row = await cursor.fetchone()
    return row["fail_count"] if row else 0

  # --- Gesehene Artikel / Outbox --------------------------------------------

  async def seen_item_ids(self, filter_id: int) -> set[int]:
    async with self._conn.execute(
      "SELECT item_id FROM seen_items WHERE filter_id = ?", (filter_id,)
    ) as cursor:
      return {row["item_id"] for row in await cursor.fetchall()}

  async def save_poll_result(
    self,
    filter_id: int,
    *,
    baseline_ids: Iterable[int] = (),
    new_items: Iterable[NewItem] = (),
    skipped_ids: Iterable[int] = (),
    max_seen_id: int | None,
  ) -> None:
    """Speichert das Ergebnis EINER Abfrage atomar (eine Transaktion).

    Erst wenn hier committet ist, gelten Artikel als gesehen. Stürzt das Programm vorher ab,
    werden sie beim nächsten Lauf erneut gefunden – es geht also nichts verloren.
    """
    now = self._iso_now()
    rows: list[tuple[int, int, str, str, str | None]] = []
    for item_id in baseline_ids:
      rows.append((filter_id, item_id, now, "baseline", None))
    for item_id in skipped_ids:
      rows.append((filter_id, item_id, now, "skipped", None))
    for new in new_items:
      payload = {
        "item": new.item.to_json_dict(),
        "seller": asdict(new.seller) if new.seller else None,
      }
      rows.append((filter_id, new.item.id, now, "pending", json.dumps(payload, ensure_ascii=False)))

    try:
      # INSERT OR IGNORE: ein schon gesehener Artikel wird nie überschrieben
      await self._conn.executemany(
        """INSERT OR IGNORE INTO seen_items
             (filter_id, item_id, first_seen_at, status, payload_json)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
      )
      await self._conn.execute(
        """UPDATE filters SET baseline_done = 1, last_success_at = ?, last_error = NULL,
                              fail_count = 0,
                              max_seen_id = MAX(COALESCE(max_seen_id, 0), COALESCE(?, 0))
           WHERE id = ?""",
        (now, max_seen_id, filter_id),
      )
      await self._conn.commit()
    except Exception:
      await self._conn.rollback()
      raise

  async def pending_outbox(self, limit: int = 20) -> list[OutboxEntry]:
    """Ungesendete Einträge, älteste zuerst."""
    async with self._conn.execute(
      """SELECT s.filter_id, s.item_id, s.payload_json, s.first_seen_at,
                f.channel_id, f.name, f.domain
         FROM seen_items s JOIN filters f ON f.id = s.filter_id
         WHERE s.status = 'pending'
         ORDER BY s.first_seen_at, s.item_id
         LIMIT ?""",
      (limit,),
    ) as cursor:
      rows = await cursor.fetchall()
    entries: list[OutboxEntry] = []
    for row in rows:
      payload = json.loads(row["payload_json"])
      seller = SellerInfo(**payload["seller"]) if payload.get("seller") else None
      entries.append(
        OutboxEntry(
          filter_id=row["filter_id"],
          item_id=row["item_id"],
          channel_id=row["channel_id"],
          filter_name=row["name"],
          domain=row["domain"],
          item=VintedItem.from_json_dict(payload["item"]),
          seller=seller,
          first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
        )
      )
    return entries

  async def mark_posted(self, filter_id: int, item_id: int) -> None:
    await self._conn.execute(
      """UPDATE seen_items SET status = 'posted', posted_at = ?, payload_json = NULL
         WHERE filter_id = ? AND item_id = ?""",
      (self._iso_now(), filter_id, item_id),
    )
    await self._conn.commit()

  async def mark_send_failed(self, filter_id: int, item_id: int, max_attempts: int) -> bool:
    """Erhöht den Versuchszähler. True, wenn der Eintrag endgültig aufgegeben wurde."""
    await self._conn.execute(
      """UPDATE seen_items
         SET attempts = attempts + 1,
             status = CASE WHEN attempts + 1 >= ? THEN 'failed' ELSE status END,
             payload_json = CASE WHEN attempts + 1 >= ? THEN NULL ELSE payload_json END
         WHERE filter_id = ? AND item_id = ?""",
      (max_attempts, max_attempts, filter_id, item_id),
    )
    await self._conn.commit()
    async with self._conn.execute(
      "SELECT status FROM seen_items WHERE filter_id = ? AND item_id = ?", (filter_id, item_id)
    ) as cursor:
      row = await cursor.fetchone()
    return row is not None and row["status"] == "failed"

  async def expire_stale_outbox(self, max_age: timedelta) -> int:
    """Verwirft ungesendete Einträge, die älter als max_age sind (keine Flut nach Ausfall)."""
    cutoff = (self._now() - max_age).isoformat()
    cursor = await self._conn.execute(
      """UPDATE seen_items SET status = 'skipped', payload_json = NULL
         WHERE status = 'pending' AND first_seen_at < ?""",
      (cutoff,),
    )
    await self._conn.commit()
    return cursor.rowcount

  async def count_pending(self) -> int:
    async with self._conn.execute(
      "SELECT COUNT(*) AS n FROM seen_items WHERE status = 'pending'"
    ) as cursor:
      row = await cursor.fetchone()
    return row["n"] if row else 0

  async def count_posted_since(self, since: datetime) -> int:
    async with self._conn.execute(
      "SELECT COUNT(*) AS n FROM seen_items WHERE status = 'posted' AND posted_at >= ?",
      (since.isoformat(),),
    ) as cursor:
      row = await cursor.fetchone()
    return row["n"] if row else 0

  async def cleanup(self, *, items_older_than: timedelta, sellers_older_than: timedelta) -> int:
    """Datensparsamkeit: alte gesehene Artikel und Verkäufer löschen. Gibt die Anzahl zurück.

    Ungesendete Einträge bleiben (die verwirft expire_stale_outbox). Alte Artikel tauchen in
    "neueste zuerst" nicht mehr auf – und falls doch, fängt sie der ID-Wasserstand ab.
    """
    now = self._now()
    items = await self._conn.execute(
      "DELETE FROM seen_items WHERE status != 'pending' AND first_seen_at < ?",
      ((now - items_older_than).isoformat(),),
    )
    sellers = await self._conn.execute(
      "DELETE FROM sellers WHERE fetched_at < ?", ((now - sellers_older_than).isoformat(),)
    )
    await self._conn.commit()
    return items.rowcount + sellers.rowcount

  # --- Schlüssel/Wert (z. B. ID der Discord-Kategorie) ------------------------

  async def get_value(self, key: str) -> str | None:
    async with self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)) as cursor:
      row = await cursor.fetchone()
    return row["value"] if row else None

  async def set_value(self, key: str, value: str) -> None:
    await self._conn.execute(
      """INSERT INTO kv (key, value) VALUES (?, ?)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
      (key, value),
    )
    await self._conn.commit()

  # --- Verkäufer-Cache ------------------------------------------------------

  async def get_seller(self, user_id: int, max_age: timedelta) -> SellerInfo | None:
    cutoff = (self._now() - max_age).isoformat()
    async with self._conn.execute(
      "SELECT * FROM sellers WHERE user_id = ? AND fetched_at >= ?", (user_id, cutoff)
    ) as cursor:
      row = await cursor.fetchone()
    if row is None:
      return None
    return SellerInfo(
      user_id=row["user_id"],
      country_iso=row["country_iso"],
      feedback_reputation=row["feedback_reputation"],
      feedback_count=row["feedback_count"],
      photo_url=row["photo_url"],
    )

  async def save_seller(self, seller: SellerInfo) -> None:
    await self._conn.execute(
      """INSERT INTO sellers (user_id, country_iso, feedback_reputation, feedback_count,
                              photo_url, fetched_at)
         VALUES (?, ?, ?, ?, ?, ?)
         ON CONFLICT(user_id) DO UPDATE SET
           country_iso = excluded.country_iso,
           feedback_reputation = excluded.feedback_reputation,
           feedback_count = excluded.feedback_count,
           photo_url = excluded.photo_url,
           fetched_at = excluded.fetched_at""",
      (
        seller.user_id,
        seller.country_iso,
        seller.feedback_reputation,
        seller.feedback_count,
        seller.photo_url,
        self._iso_now(),
      ),
    )
    await self._conn.commit()

  def _iso_now(self) -> str:
    return self._now().isoformat()


def _filter_from_row(row: aiosqlite.Row) -> FilterRow:
  return FilterRow(
    id=row["id"],
    name=row["name"],
    source_url=row["source_url"],
    canonical_url=row["canonical_url"],
    domain=row["domain"],
    api_params=json.loads(row["api_params_json"]),
    channel_id=row["channel_id"],
    countries=tuple(row["countries"].split(",")) if row["countries"] else (),
    interval_s=row["interval_s"],
    paused=bool(row["paused"]),
    baseline_done=bool(row["baseline_done"]),
    max_seen_id=row["max_seen_id"],
    last_success_at=datetime.fromisoformat(row["last_success_at"])
    if row["last_success_at"]
    else None,
    last_error=row["last_error"],
    fail_count=row["fail_count"],
  )
