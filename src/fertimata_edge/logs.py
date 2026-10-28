"""Logging terstruktur ke stdout (ditangkap journald di Jetson).

Format satu baris `waktu level logger pesan kunci=nilai`, supaya mudah di-grep di `journalctl`.
Data terstruktur diberikan lewat `extra=fields(...)`.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

_FIELDS_ATTR = "fertimata_fields"


def fields(**values: Any) -> dict[str, Any]:
    """Pemakaian: `log.info("relay ditulis", extra=fields(unit=20, ch=0))`."""
    return {_FIELDS_ATTR: values}


def _format_value(value: Any) -> str:
    text = str(value)
    if text == "" or any(c in text for c in ' "=\n\t'):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return text


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        moment = datetime.fromtimestamp(record.created, timezone.utc)
        stamp = moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
        parts = [stamp, record.levelname, record.name, record.getMessage()]
        extra = getattr(record, _FIELDS_ATTR, None)
        if extra:
            parts.extend(f"{key}={_format_value(val)}" for key, val in extra.items())
        line = " ".join(parts)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(level: str, stream: TextIO | None = None) -> None:
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(KeyValueFormatter())
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level.upper())
