"""Structured logging for the system itself (never confuse with ingested logs)."""
from __future__ import annotations

import logging
import sys

import structlog

_configured = False


def configure(level: str = "INFO", json_output: bool = True) -> None:
    global _configured
    if _configured:
        return
    logging.basicConfig(stream=sys.stderr, level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")
    renderer = structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
