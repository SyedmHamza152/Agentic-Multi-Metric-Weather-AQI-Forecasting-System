"""Logging setup: one format, printed to the console and saved to logs/agentweather.log."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Union

_ROOT_NAME = "agentweather"
_configured = False


def setup_logging(level: str = "INFO", log_dir: Optional[Union[str, Path]] = None) -> None:
    """Configure logging once. Safe to call many times."""
    global _configured
    if _configured:
        return

    logger = logging.getLogger(_ROOT_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_dir is not None:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path / "agentweather.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger that lives under the 'agentweather' logger."""
    if name.startswith(_ROOT_NAME):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_NAME}.{name}")
