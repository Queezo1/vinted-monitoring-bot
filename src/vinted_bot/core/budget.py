"""Verteilt das gemeinsame Anfrage-Budget auf die Filter – nach Priorität.

Alle Filter teilen sich EINE IP und damit ein Budget: höchstens 1 Anfrage pro
`spacing_s`. Davon geht ein Teil (1 / CATALOG_BUDGET_FACTOR) an Katalog-Abfragen, der Rest
bleibt für Länder-Lookups und das Nachladen von Fotos.

Das eingestellte Intervall eines Filters ist sein Wunsch – und zugleich sein Gewicht:
  Filter A: 10 s  -> wünscht 0,100 Abfragen/s
  Filter B: 120 s -> wünscht 0,008 Abfragen/s
Passen alle Wünsche ins Budget, bekommt jeder genau sein Intervall. Sonst werden ALLE im
gleichen Verhältnis gestreckt – ein wichtiger 10-s-Filter also viel weniger als früher, wo
jeder Filter gleich viel bekam. Unwichtige Filter auf 60–120 s zu stellen, macht damit die
wichtigen schneller.
"""

from __future__ import annotations

CATALOG_BUDGET_FACTOR = 1.5


def effective_intervals(
  wanted: dict[int, float], spacing_s: float, factor: float = CATALOG_BUDGET_FACTOR
) -> dict[int, float]:
  """Filter-ID -> gewünschtes Intervall  =>  Filter-ID -> tatsächliches Intervall."""
  if not wanted:
    return {}
  budget_per_s = 1 / (spacing_s * factor)  # Katalog-Abfragen pro Sekunde insgesamt
  demand_per_s = sum(1 / interval for interval in wanted.values())
  stretch = max(1.0, demand_per_s / budget_per_s)
  return {filter_id: interval * stretch for filter_id, interval in wanted.items()}
