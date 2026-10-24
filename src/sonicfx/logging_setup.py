"""Structured logging setup for SonicFX (stdlib only, no I/O in audio path).

Provides:
* ``setup_logging()`` — console + rotating JSONL file handler under
  ``~/.local/state/sonicfx/logs/`` (XDG-compliant), safe to call many times.
* :class:`JSONFormatter` — machine-readable one-object-per-line records for the
  diagnostics panel; human text still goes to the console.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
from pathlib import Path

_APP_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "sonicfx"
_LOG_DIR = _APP_DIR / "logs"
_MAX_BYTES = 1_000_000
_BACKUPS = 3

_TEXT_FMT = "%(asctime)sZ %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per record (never raises)."""

    _EXTRA_KEYS = ("status", "node_id", "subject", "duration_ms", "path")

    def format(self, record: logging.LogRecord) -> str:  # noqa: D102
        import time

        payload = {
            "ts": time.strftime(_DATEFMT, time.gmtime(record.created)),
            "ts_ms": int(record.msecs),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        for key in self._EXTRA_KEYS:
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # never crash logging
            return json.dumps({"ts": payload["ts"], "level": "ERROR",
                               "message": "unserialisable log record"})


def setup_logging(level: int = logging.INFO, console: bool = True) -> Path:
    """Idempotently install the app logging config. Returns the log directory."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("sonicfx")
    root.setLevel(level)
    root.propagate = False

    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        fh = logging.handlers.RotatingFileHandler(
            _LOG_DIR / "sonicfx.log", maxBytes=_MAX_BYTES, backupCount=_BACKUPS,
            encoding="utf-8")
        fh.setFormatter(JSONFormatter())
        fh.setLevel(level)
        root.addHandler(fh)

    if console and not any(isinstance(h, logging.StreamHandler) and not
                           isinstance(h, logging.handlers.RotatingFileHandler)
                           for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter(_TEXT_FMT, datefmt=_DATEFMT))
        sh.setLevel(level)
        root.addHandler(sh)

    # Quieten noisy third-party loggers used by our control-plane subprocesses.
    for noisy in ("fontTools", "matplotlib", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return _LOG_DIR


def get_logger(name: str = __name__) -> logging.Logger:
    return logging.getLogger(f"sonicfx.{name}")
