"""Fehlerklassen für Vinted-Anfragen.

Jede Klasse steht für eine andere Reaktion (siehe PLAN.md, Abschnitt 3.3). Einen 403 wie
einen Timeout zu wiederholen würde aus einer kurzen Sperre eine lange machen.
"""

from __future__ import annotations


class VintedError(Exception):
  """Basisklasse aller Vinted-Fehler."""


class UnauthorizedError(VintedError):
  """401: Das anonyme Session-Token ist abgelaufen -> neue Session holen."""


class BlockedError(VintedError):
  """403 oder Challenge-Seite: Wir werden abgewiesen -> lange Pause, Admin-Meldung."""


class RateLimitedError(VintedError):
  """429: Zu schnell -> mindestens `retry_after_s` warten."""

  def __init__(self, message: str, retry_after_s: float | None) -> None:
    super().__init__(message)
    self.retry_after_s = retry_after_s


class MalformedResponseError(VintedError):
  """200, aber nicht das erwartete Format -> API geändert? Kein Retry, Alarm."""


class VintedNetworkError(VintedError):
  """Timeout, DNS, Verbindungsabbruch -> kurzer Backoff."""


class UnexpectedStatusError(VintedError):
  """Jeder andere Statuscode (400, 404, 5xx ...)."""

  def __init__(self, message: str, status_code: int) -> None:
    super().__init__(message)
    self.status_code = status_code
