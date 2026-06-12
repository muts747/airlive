"""Application bootstrap: DB initialization and background collection worker."""

from __future__ import annotations

import database
import data_engine

_started = False


def ensure_backend_started() -> None:
    """Initialize SQLite and start the 2-minute background polling loop once."""
    global _started
    if _started:
        return

    database.init_db()
    data_engine.start_background_scheduler()
    _started = True
