"""Structured logging. Use get_logger("TRACKER") etc; every record is prefixed
with the component tag so logs can be filtered/aggregated by subsystem.

Two handlers attached by default:
  - StreamHandler → stdout (so PowerShell shows the live feed)
  - RotatingFileHandler → backend/logs/bot.log (so we have history when the
    terminal scrolls past or gets closed — critical for diagnosing silent
    stalls after the fact)
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from src.config.settings import settings


_CONFIGURED = False

# Log file lives next to main.py so it travels with the backend directory.
# 5 MB per file × 5 rotations ≈ 25 MB ceiling — plenty of history without
# filling the disk on a small VPS.
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_LOG_FILE = _LOG_DIR / "bot.log"
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUP_COUNT = 5


class _PrefixedAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[{self.extra['component']}] {msg}", kwargs


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    fmt = logging.Formatter("%(asctime)s %(levelname)s :: %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    handlers: list[logging.Handler] = [stream_handler]

    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            _LOG_FILE,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        handlers.append(file_handler)
    except OSError as e:
        # If the logs dir can't be created (read-only FS, permissions),
        # don't crash startup — stdout still works.
        stream_handler.handle(logging.LogRecord(
            name="logging", level=logging.WARNING, pathname=__file__, lineno=0,
            msg=f"file logging disabled: {e}", args=(), exc_info=None,
        ))

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)
    _CONFIGURED = True


def get_logger(component: str) -> logging.LoggerAdapter:
    """Logger that prefixes every message with [COMPONENT]. Component should be
    one of: TRACKER, RISK, SIMULATION, EXECUTION, DATABASE, API, BOT_MANAGER,
    AUTH, ANALYTICS, WALLET."""
    configure_logging()
    base = logging.getLogger(component.lower())
    return _PrefixedAdapter(base, extra={"component": component.upper()})
