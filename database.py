"""SQLite persistence layer for GPS disruption tracking."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "gps_tracker.db"

CREATE_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS gps_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    region TEXT NOT NULL,
    gps_index INTEGER NOT NULL,
    disruption_type TEXT NOT NULL,
    affected_planes INTEGER NOT NULL,
    total_planes INTEGER NOT NULL
);
"""

CREATE_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS collection_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_gps_logs_region_timestamp
ON gps_logs (region, timestamp DESC);
"""


def init_db(db_path: Path | str = DB_PATH) -> None:
    """Initialize database schema."""
    with _connect(db_path) as conn:
        conn.executescript(CREATE_LOGS_TABLE + CREATE_METADATA_TABLE + CREATE_INDEX)
        conn.commit()


@contextmanager
def _connect(db_path: Path | str = DB_PATH):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def set_last_collection_time(
    timestamp: datetime,
    db_path: Path | str = DB_PATH,
) -> None:
    """Record the timestamp of the last successful collection cycle."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collection_metadata (key, value)
            VALUES ('last_collection_time', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (timestamp.strftime("%Y-%m-%d %H:%M:%S"),),
        )
        conn.commit()


def get_last_collection_time(db_path: Path | str = DB_PATH) -> str | None:
    """Return the last successful collection timestamp string, if any."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM collection_metadata WHERE key = 'last_collection_time'"
        ).fetchone()
    return row["value"] if row else None


def insert_reading(
    timestamp: datetime,
    region: str,
    gps_index: int,
    disruption_type: str,
    affected_planes: int,
    total_planes: int,
    db_path: Path | str = DB_PATH,
) -> None:
    """Append one region reading to the historical log."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO gps_logs (
                timestamp, region, gps_index, disruption_type,
                affected_planes, total_planes
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                region,
                gps_index,
                disruption_type,
                affected_planes,
                total_planes,
            ),
        )
        conn.commit()


def insert_readings_batch(
    readings: list[dict[str, Any]],
    db_path: Path | str = DB_PATH,
) -> None:
    """Insert multiple readings in a single transaction."""
    if not readings:
        return

    rows = [
        (
            reading["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
            reading["region"],
            reading["gps_index"],
            reading["disruption_type"],
            reading["affected_planes"],
            reading["total_planes"],
        )
        for reading in readings
    ]

    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO gps_logs (
                timestamp, region, gps_index, disruption_type,
                affected_planes, total_planes
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def get_latest_readings_per_region(
    db_path: Path | str = DB_PATH,
) -> dict[str, dict[str, Any]]:
    """Return the most recent historical reading for each region."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT g.timestamp, g.region, g.gps_index, g.disruption_type,
                   g.affected_planes, g.total_planes
            FROM gps_logs g
            INNER JOIN (
                SELECT region, MAX(timestamp) AS max_ts
                FROM gps_logs
                GROUP BY region
            ) latest
            ON g.region = latest.region AND g.timestamp = latest.max_ts
            ORDER BY g.region
            """
        ).fetchall()

    return {row["region"]: dict(row) for row in rows}


def get_history_last_24_hours(
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    """Return all log entries from the past 24 hours, newest first."""
    cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT timestamp, region, gps_index, disruption_type,
                   affected_planes, total_planes
            FROM gps_logs
            WHERE timestamp >= ?
            ORDER BY timestamp DESC, region ASC
            """,
            (cutoff,),
        ).fetchall()
    return [dict(row) for row in rows]


def clear_all_history(db_path: Path | str = DB_PATH) -> None:
    """Wipe all historical tracking data."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM gps_logs")
        conn.execute("DELETE FROM collection_metadata")
        conn.commit()


def get_log_count(db_path: Path | str = DB_PATH) -> int:
    """Return total number of stored log rows."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM gps_logs").fetchone()
    return int(row["cnt"])


def get_latest_cycle_readings(
    db_path: Path | str = DB_PATH,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Return readings from the most recent collection timestamp."""
    last_ts = get_last_collection_time(db_path=db_path)
    if not last_ts:
        return None, []

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT timestamp, region, gps_index, disruption_type,
                   affected_planes, total_planes
            FROM gps_logs
            WHERE timestamp = ?
            ORDER BY region
            """,
            (last_ts,),
        ).fetchall()

    return last_ts, [dict(row) for row in rows]
