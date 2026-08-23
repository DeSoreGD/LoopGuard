"""Logging setup for the local desktop application."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from selfboss.core.models import AppSettings


def configure_logging(
    settings: AppSettings, *, level: int = logging.INFO
) -> logging.Logger:
    """Configure file logging under the app log directory."""
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("selfboss")
    logger.setLevel(level)
    logger.propagate = False

    log_path = settings.log_dir / "selfboss.log"
    existing_paths = {
        getattr(handler, "baseFilename", None) for handler in logger.handlers
    }
    if str(log_path) not in existing_paths:
        handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)

    return logger
