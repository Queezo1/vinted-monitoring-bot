"""Reine Logik: Welche Artikel einer Abfrage sind wirklich neu?

Keine Datenbank, kein Netzwerk – nur Daten rein, Entscheidung raus. Dadurch leicht testbar.

Die Tore, die ein Artikel passieren muss, um gepostet zu werden:
  1. Baseline: Beim allerersten Lauf eines Filters wird nichts gepostet, alles gilt als gesehen.
  2. Schon gesehen (ID in der Datenbank)? -> ignorieren.
  3. Hochgeschoben ("promoted")? -> alter Artikel, der wieder oben auftaucht -> nicht posten.
  4. ID-Wasserstand: Vinted liefert seit 09/2026 keinen Upload-Zeitstempel mehr. Artikel-IDs
     steigen aber mit der Zeit. Liegt eine "neue" ID weit unter der höchsten je gesehenen,
     ist es fast sicher ein alter Artikel (z. B. reaktiviert oder Preis geändert).
  5. Obergrenze pro Abfrage: Nach langer Pause keine Flut, nur die neuesten N.
Der Länderfilter folgt danach im Poller, weil er einen Netzwerk-Request braucht.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vinted_bot.vinted.models import VintedItem

# Vinted-IDs sind ~10,4 Mrd. (10/2026). Die Toleranz deckt grob mehrere Tage Neuzugänge ab.
# Bewusst großzügig: lieber einmal einen alten Artikel posten als einen neuen verschlucken.
# Wird im M2-Dauerlauf anhand der Logs ("Wasserstand") überprüft.
ID_TOLERANCE = 20_000_000
MAX_POSTS_PER_POLL = 10


@dataclass
class DedupeResult:
  baseline_ids: list[int] = field(default_factory=list)  # erster Lauf: nur merken
  new_items: list[VintedItem] = field(default_factory=list)  # zu posten, älteste zuerst
  skipped_ids: list[int] = field(default_factory=list)  # gesehen, aber nicht posten
  max_seen_id: int | None = None


def select_new(
  items: list[VintedItem],
  seen_ids: set[int],
  *,
  baseline_done: bool,
  max_seen_id: int | None,
  id_tolerance: int = ID_TOLERANCE,
  max_posts: int = MAX_POSTS_PER_POLL,
) -> DedupeResult:
  result = DedupeResult()
  unseen = [item for item in items if item.id not in seen_ids]
  ids_this_poll = [item.id for item in items]
  result.max_seen_id = max([*ids_this_poll, max_seen_id or 0]) or None

  if not baseline_done:
    result.baseline_ids = [item.id for item in unseen]
    return result

  candidates: list[VintedItem] = []
  for item in unseen:
    if item.promoted:
      result.skipped_ids.append(item.id)
    elif max_seen_id is not None and item.id < max_seen_id - id_tolerance:
      result.skipped_ids.append(item.id)
    else:
      candidates.append(item)

  # Vinted liefert "neueste zuerst" -> die ersten max_posts behalten, Rest überspringen
  result.skipped_ids.extend(item.id for item in candidates[max_posts:])
  # In Discord sollen ältere zuerst erscheinen, damit der neueste unten steht
  result.new_items = list(reversed(candidates[:max_posts]))
  return result
