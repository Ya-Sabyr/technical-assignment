"""Optional JSON log formatter for production deployments.

The orchestrator emits its log messages via the stdlib `logging` module
under the `dexter_sync.sync` logger and tags every record with `run_id`
via a LoggerAdapter (see `sync.py`). By default no handler is configured —
the consumer chooses the format.

`configure_json_logging()` is a small helper that wires a JSON-line
formatter onto the package logger. It is intentionally not auto-applied;
calling code (an entrypoint, a test) opts in.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import IO

from dexter_sync.conf import LOGGER_NAME

# Standard LogRecord attributes we don't want to bury under "extra" — the
# formatter promotes our `run_id` and any other custom keys explicitly.
_LOGRECORD_BUILTIN_ATTRS = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
}


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record, one line at a time.

    Includes `timestamp`, `level`, `logger`, `message`, and any custom
    attributes attached via `extra={...}` (notably `run_id`). Exception
    info is rendered into a `traceback` field if present.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_BUILTIN_ATTRS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_json_logging(
    *,
    level: int = logging.INFO,
    stream: IO[str] | None = None,
    logger_name: str = LOGGER_NAME,
    propagate: bool = False,
) -> logging.Handler:
    """Attach a JSON-line stream handler to the package logger.

    Returns the handler so callers can detach it in tests
    (`logger.removeHandler`). Idempotent: calling this twice replaces the
    previous handler installed by this function.

    `propagate` defaults to False so applications that already configure
    the root logger don't double-emit each line. Pass `propagate=True`
    explicitly if you want the package log records to bubble up to the
    root handlers as well.
    """
    logger = logging.getLogger(logger_name)
    for existing in [h for h in logger.handlers if getattr(h, "_dexter_json", False)]:
        logger.removeHandler(existing)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler.setLevel(level)
    handler._dexter_json = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = propagate
    return handler
