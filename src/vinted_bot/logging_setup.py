"""Logging: Konsole (falls vorhanden) + rotierende Logdatei."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str, log_dir: Path) -> None:
  log_dir.mkdir(parents=True, exist_ok=True)
  formatter = logging.Formatter(_FORMAT)

  root = logging.getLogger()
  root.handlers.clear()
  root.setLevel(level)

  # Unter pythonw (Autostart ohne Fenster) gibt es keine Konsole: sys.stderr ist None
  if sys.stderr is not None:
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

  # Max. 5 Dateien à 2 MB, danach wird die älteste überschrieben
  file_handler = RotatingFileHandler(
    log_dir / "bot.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
  )
  file_handler.setFormatter(formatter)
  root.addHandler(file_handler)

  numeric_level = logging.getLevelNamesMapping()[level]
  # discord.py und httpx sind auf DEBUG sehr gesprächig (httpx loggt u. a. komplette URLs)
  for noisy in ("discord", "httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(max(numeric_level, logging.INFO))
  # Die Sprach-Warnungen (PyNaCl/davey) sind für diesen Bot irrelevant
  logging.getLogger("discord.client").setLevel(max(numeric_level, logging.ERROR))
