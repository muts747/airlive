"""Tests for anomaly detection and region analysis logic."""

from __future__ import annotations

import unittest
from unittest import mock
from unittest.mock import patch

import data_engine


class DetectionTests(unittest.TestCase):
    def test_spoofed_by_zero_ground_speed_at_cruise(self) -> None:
        aircraft = {"alt_baro": 35000, "gs": 0, "lat": 31.5, "lon": 35.0}
        self.assertTrue(data_engine._is_spoofed(aircraft))

    def test_spoofed_by_airport_coordinate_lock(self) -> None:
        aircraft = {
            "alt_baro": 25000,
            "gs": 450,
            "lat": 32.01,
            "lon": 34.88,
        }
        self.assertTrue(data_engine._is_spoofed(aircraft))

    def test_not_spoofed_normal_flight(self) -> None:
        aircraft = {"alt_baro": 35000, "gs": 450, "lat": 31.5, "lon": 35.0}
        self.assertFalse(data_engine._is_spoofed(aircraft))

    def test_jammed_by_gps_ok_before(self) -> None:
        aircraft = {"gpsOkBefore": 1710000000, "lat": 31.5, "lon": 35.0}
        self.assertTrue(data_engine._is_jammed(aircraft))

    def test_jammed_by_missing_position(self) -> None:
        aircraft = {"lat": None, "lon": None, "lastPosition": {}}
        self.assertTrue(data_engine._is_jammed(aircraft))

    def test_jammed_by_stale_position(self) -> None:
        aircraft = {"lat": 31.5, "lon": 35.0, "seen_pos": 45}
        self.assertTrue(data_engine._is_jammed(aircraft))

    def test_not_jammed_fresh_position(self) -> None:
        aircraft = {"lat": 31.5, "lon": 35.0, "seen_pos": 2}
        self.assertFalse(data_engine._is_jammed(aircraft))

    def test_disruption_type_priority(self) -> None:
        self.assertEqual(
            data_engine._determine_disruption_type(3, 1), "Spoofing"
        )
        self.assertEqual(
            data_engine._determine_disruption_type(1, 3), "Jamming"
        )
        self.assertEqual(data_engine._determine_disruption_type(0, 0), "None")


class RegionAnalysisTests(unittest.TestCase):
    def test_build_point_url_matches_spec(self) -> None:
        url = data_engine._build_point_url(40.7128, -74.0060, 50)
        self.assertEqual(
            url,
            "https://api.airplanes.live/v2/point/40.7128/-74.006/50",
        )

    def test_israel_regions_have_50km_buffer(self) -> None:
        name = "Israel - Tel Aviv & Central District"
        base = data_engine._BASE_REGIONS[name]
        expanded = data_engine.REGIONS[name]
        self.assertLess(expanded["lat_min"], base["lat_min"])
        self.assertGreater(expanded["lat_max"], base["lat_max"])
        self.assertLess(expanded["lon_min"], base["lon_min"])
        self.assertGreater(expanded["lon_max"], base["lon_max"])

    def test_non_israel_regions_unchanged(self) -> None:
        self.assertEqual(
            data_engine.REGIONS["Kuwait"],
            data_engine._BASE_REGIONS["Kuwait"],
        )

    def test_bounds_to_point_query(self) -> None:
        bounds = data_engine.REGIONS["Kuwait"]
        lat, lon, radius = data_engine._bounds_to_point_query(bounds)
        self.assertAlmostEqual(lat, 29.3)
        self.assertAlmostEqual(lon, 47.5)
        self.assertGreaterEqual(radius, 1)
        self.assertLessEqual(radius, data_engine.MAX_RADIUS_NM)

    def test_fetch_region_aircraft_uses_v2_point(self) -> None:
        bounds = data_engine.REGIONS["Kuwait"]
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "ac": [{"hex": "abc123", "lat": 29.0, "lon": 47.0, "seen_pos": 1}]
        }
        mock_response.raise_for_status = mock.Mock()

        with patch.object(data_engine.requests, "get", return_value=mock_response) as get:
            aircraft = data_engine.fetch_region_aircraft(bounds)

        get.assert_called_once()
        self.assertIn("/v2/point/", get.call_args.args[0])
        self.assertEqual(len(aircraft), 1)

    def test_test_api_connection_success(self) -> None:
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"total": 42, "ac": [{}] * 42}

        with patch.object(data_engine.requests, "get", return_value=mock_response):
            self.assertTrue(data_engine.test_api_connection())

    def test_test_api_connection_failure(self) -> None:
        mock_response = mock.Mock()
        mock_response.status_code = 503

        with patch.object(data_engine.requests, "get", return_value=mock_response):
            self.assertFalse(data_engine.test_api_connection())

    def test_analyze_region_no_aircraft(self) -> None:
        bounds = data_engine.REGIONS["Kuwait"]
        with patch.object(data_engine, "fetch_region_aircraft", return_value=[]):
            result = data_engine.analyze_region("Kuwait", bounds)

        self.assertFalse(result.has_live_data)
        self.assertIsNone(result.gps_index)
        self.assertEqual(result.status_label, "No Data Available")

    def test_analyze_region_computes_index(self) -> None:
        bounds = data_engine.REGIONS["Kuwait"]
        aircraft = [
            {"hex": "aaa111", "alt_baro": 35000, "gs": 0, "lat": 29.0, "lon": 47.0},
            {"hex": "bbb222", "gpsOkBefore": 1710000000, "lat": 29.1, "lon": 47.1},
            {"hex": "ccc333", "lat": 29.2, "lon": 47.2, "seen_pos": 1},
        ]
        with patch.object(data_engine, "fetch_region_aircraft", return_value=aircraft):
            result = data_engine.analyze_region("Kuwait", bounds)

        self.assertTrue(result.has_live_data)
        self.assertEqual(result.total_planes, 3)
        self.assertEqual(result.affected_planes, 2)
        self.assertEqual(result.gps_index, 67)
        self.assertEqual(result.disruption_type, "Spoofing")

    def test_region_count(self) -> None:
        self.assertEqual(len(data_engine.REGIONS), 12)


if __name__ == "__main__":
    unittest.main()
