"""Logging setup helpers for the long-form pipeline."""
from __future__ import annotations

import logging
from logging import Logger
from pathlib import Path
from typing import Optional


def configure_logging(level: str, log_file: Path) -> Logger:
    """Configure root logger with console + file handlers."""
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers to avoid duplicate logs during repeated runs
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(file_handler)

    logger.debug("Logging configured", extra={"level": level, "file": str(log_file)})
    return logger


def get_logger(name: Optional[str] = None) -> Logger:
    """Return a module-level logger."""
    return logging.getLogger(name)
