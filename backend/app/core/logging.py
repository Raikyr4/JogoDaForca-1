import json
import logging
import os
import time
from typing import Any


class JsonFormatter(logging.Formatter):
    _skip_fields = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }

    def __init__(self, server_id: str) -> None:
        super().__init__()
        self.server_id = server_id

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "server_id": self.server_id,
        }
        for key, value in record.__dict__.items():
            if key in self._skip_fields or key.startswith("_"):
                continue
            payload[key] = value
        return json.dumps(payload, ensure_ascii=True)


def setup_logging(server_id: str) -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(server_id=server_id))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
