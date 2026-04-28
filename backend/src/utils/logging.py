"""Structured logging. Use get_logger("TRACKER") etc; every record is prefixed
with the component tag so logs can be filtered/aggregated by subsystem."""
import logging
import sys

from src.config.settings import settings


_CONFIGURED = False


class _PrefixedAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[{self.extra['component']}] {msg}", kwargs


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s :: %(message)s"))
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.handlers.clear()
    root.addHandler(handler)
    _CONFIGURED = True


def get_logger(component: str) -> logging.LoggerAdapter:
    """Logger that prefixes every message with [COMPONENT]. Component should be
    one of: TRACKER, RISK, SIMULATION, EXECUTION, DATABASE, API, BOT_MANAGER,
    AUTH, ANALYTICS."""
    configure_logging()
    base = logging.getLogger(component.lower())
    return _PrefixedAdapter(base, extra={"component": component.upper()})
