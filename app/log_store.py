"""Shared in-memory log record buffer.

This module holds the global buffer used by ListHandler (in app/__init__.py)
and the /logs endpoint (in app/routes/logs.py) so that neither layer has to
import from the other.

Note: Each gunicorn worker maintains its own copy of this buffer.  GET /logs
will therefore only return records captured by the worker that handles that
particular request.
"""

import threading
from collections import deque

# Bounded + thread-safe appends (deque.append is atomic under the GIL, and
# maxlen eviction is O(1) — unlike `del log_records[:-200]` which is O(n)
# and racy across gthread workers). A lock guards multi-step read paths.
log_records: deque = deque(maxlen=200)
log_records_lock = threading.Lock()
