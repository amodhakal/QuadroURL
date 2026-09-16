"""Single home for all logging setup (#152).

``JsonFormatter``, ``ListHandler`` and ``configure_logging`` (moved here
verbatim from ``app/__init__.py``) are the app's real logging setup.
``app/__init__.py`` re-exports them so ``from app import ListHandler``
keeps working.

``JSONFormatter`` / ``setup_logger`` are kept as backward-compatible
helpers (``tests/test_logger.py`` imports them). They are NOT aliases of
``JsonFormatter`` because their outputs differ:

- ``JsonFormatter`` emits ``timestamp`` (UTC ISO-8601), ``level``,
  ``logger``, ``message``, plus ``method``/``path``/``remote_addr``/
  ``request_id`` inside a Flask request context and ``exception`` when
  ``exc_info`` is set.
- ``JSONFormatter`` emits ``timestamp`` (via ``formatTime``), ``level``,
  ``message`` and ``module`` — no request context, no ``logger`` name.
"""

import json
import logging
import os
from datetime import datetime, timezone

from flask import request

from app.log_store import log_records


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        try:
            from app.utils.request_ctx import get_client_ip, get_request_id

            log_data["method"] = request.method
            log_data["path"] = request.path
            log_data["remote_addr"] = get_client_ip()
            log_data["request_id"] = get_request_id()
        except RuntimeError:
            pass
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


class ListHandler(logging.Handler):
    """In-memory log handler that stores records as structured dicts.

    Delegates exception formatting to a ``logging.Formatter`` instance so
    that ``formatException`` is available (it is defined on ``Formatter``,
    not ``Handler``).
    """

    _formatter = logging.Formatter()

    def emit(self, record):
        try:
            log_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            try:
                from app.utils.request_ctx import get_client_ip, get_request_id

                log_data["method"] = request.method
                log_data["path"] = request.path
                log_data["remote_addr"] = get_client_ip()
                log_data["request_id"] = get_request_id()
            except RuntimeError:
                pass
            if record.exc_info:
                log_data["exception"] = self._formatter.formatException(record.exc_info)
            log_records.append(log_data)
        except Exception:
            self.handleError(record)


def configure_logging(app):
    log_level = (
        logging.DEBUG if os.environ.get("LOG_LEVEL", "").upper() == "DEBUG" else logging.INFO
    )
    json_handler = logging.StreamHandler()
    json_handler.setFormatter(JsonFormatter())
    list_handler = ListHandler()

    app.logger.handlers.clear()
    app.logger.addHandler(json_handler)
    app.logger.addHandler(list_handler)
    app.logger.setLevel(log_level)
    app.logger.propagate = False

    for name in ("", "werkzeug", "peewee"):
        logger = logging.getLogger(name) if name else logging.getLogger()
        if not logger.handlers:
            logger.addHandler(json_handler)
            logger.addHandler(list_handler)
            logger.setLevel(log_level)
            if name:
                logger.propagate = False


class JSONFormatter(logging.Formatter):
    """Legacy JSON formatter kept for backward compatibility.

    Emits ``timestamp``/``level``/``message``/``module`` (see module
    docstring for why this is not an alias of ``JsonFormatter``).
    """

    def format(self, record):
        return json.dumps(
            {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "message": record.getMessage(),
                "module": record.module,
            }
        )


def setup_logger(name="quadroPE"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)

    return logger
