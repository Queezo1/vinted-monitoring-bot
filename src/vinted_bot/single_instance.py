"""Stellt sicher, dass der Bot nur EINMAL läuft (sonst doppelte Posts und doppelte Last).

Technik: Eine Sperrdatei wird exklusiv gesperrt, solange der Prozess lebt. Das Betriebssystem
gibt die Sperre automatisch frei, wenn der Prozess endet – auch bei Absturz oder Abschalten.
In der Datei steht die Prozess-ID (PID), damit scripts/stop_bot.ps1 den Bot beenden kann.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import IO


class AlreadyRunningError(RuntimeError):
  pass


class InstanceLock:
  def __init__(self, path: Path) -> None:
    self.path = path
    self._file: IO[str] | None = None

  def acquire(self) -> None:
    self.path.parent.mkdir(parents=True, exist_ok=True)
    # "a+" statt "w": eine laufende Instanz darf ihre PID nicht vom zweiten Versuch verlieren
    handle = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115 – bleibt bewusst offen
    try:
      _lock(handle)
    except OSError as error:
      handle.close()
      raise AlreadyRunningError(
        f"Der Bot läuft bereits (Sperrdatei {self.path} ist belegt)."
      ) from error
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    self._file = handle

  def release(self) -> None:
    if self._file is None:
      return
    try:
      _unlock(self._file)
    finally:
      self._file.close()
      self._file = None

  def __enter__(self) -> InstanceLock:
    self.acquire()
    return self

  def __exit__(self, *exc: object) -> None:
    self.release()


# Sperren ist betriebssystemabhängig: Windows nutzt msvcrt, Linux/macOS fcntl.
if sys.platform == "win32":
  import msvcrt

  # Gesperrt wird 1 Byte WEIT HINTER dem Dateiinhalt. Unter Windows blockiert eine Sperre
  # nämlich auch das Lesen – so bleibt die PID am Dateianfang für stop_bot.ps1 lesbar.
  _LOCK_OFFSET = 1 << 20

  def _lock(handle: IO[str]) -> None:
    handle.seek(_LOCK_OFFSET)
    # LK_NBLCK wirft sofort OSError, wenn schon gesperrt (statt zu warten)
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

  def _unlock(handle: IO[str]) -> None:
    handle.seek(_LOCK_OFFSET)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
  import fcntl

  def _lock(handle: IO[str]) -> None:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

  def _unlock(handle: IO[str]) -> None:
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
