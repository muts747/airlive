"""Tests for the SQLite persistence layer."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import database


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_gps_tracker.db"
        database.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_insert_and_fetch_latest_per_region(self) -> None:
        ts = datetime(2026, 6, 11, 12, 0, 0)
        database.insert_reading(
            ts, "Jordan", 25, "Jamming", 3, 12, db_path=self.db_path
        )
        database.insert_reading(
            ts - timedelta(hours=1),
            "Jordan",
            10,
            "None",
            0,
            8,
            db_path=self.db_path,
        )

        latest = database.get_latest_readings_per_region(db_path=self.db_path)
        self.assertIn("Jordan", latest)
        self.assertEqual(latest["Jordan"]["gps_index"], 25)
        self.assertEqual(latest["Jordan"]["disruption_type"], "Jamming")

    def test_batch_insert_and_latest_cycle(self) -> None:
        ts = datetime(2026, 6, 11, 14, 30, 0)
        database.insert_readings_batch(
            [
                {
                    "timestamp": ts,
                    "region": "Iraq",
                    "gps_index": 40,
                    "disruption_type": "Spoofing",
                    "affected_planes": 4,
                    "total_planes": 10,
                },
                {
                    "timestamp": ts,
                    "region": "Kuwait",
                    "gps_index": 0,
                    "disruption_type": "None",
                    "affected_planes": 0,
                    "total_planes": 5,
                },
            ],
            db_path=self.db_path,
        )
        database.set_last_collection_time(ts, db_path=self.db_path)

        last_ts, cycle = database.get_latest_cycle_readings(db_path=self.db_path)
        self.assertEqual(last_ts, "2026-06-11 14:30:00")
        self.assertEqual(len(cycle), 2)
        self.assertEqual(cycle[0]["region"], "Iraq")

    def test_history_last_24_hours_and_clear(self) -> None:
        now = datetime.now()
        database.insert_reading(
            now, "Jordan", 15, "Jamming", 1, 6, db_path=self.db_path
        )
        database.insert_reading(
            now - timedelta(hours=30),
            "Jordan",
            99,
            "Spoofing",
            9,
            9,
            db_path=self.db_path,
        )

        history = database.get_history_last_24_hours(db_path=self.db_path)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["gps_index"], 15)

        database.set_last_collection_time(now, db_path=self.db_path)
        database.clear_all_history(db_path=self.db_path)
        self.assertEqual(database.get_log_count(db_path=self.db_path), 0)
        self.assertIsNone(database.get_last_collection_time(db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
