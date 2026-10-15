"""Logging: Konsole + rotierende Logdatei."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str, log_dir: Path) -> None:
  log_dir.mkdir(parents=True, exist_ok=True)
  formatter = logging.Formatter(_FORMAT)

  console = logging.StreamHandler()
  console.setFormatter(formatter)

  # Max. 5 Dateien à 2 MB, danach wird die älteste überschrieben
  file_handler = RotatingFileHandler(
    log_dir / "bot.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
  )
  file_handler.setFormatter(formatter)

  root = logging.getLogger()
  root.handlers.clear()
  root.setLevel(level)
  root.addHandler(console)
  root.addHandler(file_handler)

  # discord.py und httpx sind auf DEBUG sehr gesprächig (httpx loggt u. a. komplette URLs)
  numeric_level = logging.getLevelNamesMapping()[level]
  for noisy in ("discord", "httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(max(numeric_level, logging.INFO))
