"""API fetching, anomaly detection, and background collection for GPS disruptions."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from apscheduler.schedulers.background import BackgroundScheduler

import database

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.airplanes.live/v2"
API_TIMEOUT_SECONDS = 15
API_RATE_LIMIT_SECONDS = 1.1
POLL_INTERVAL_MINUTES = 2
MAX_RADIUS_NM = 250
EARTH_RADIUS_NM = 3440.065

CONNECTION_TEST_LAT = 40.7128
CONNECTION_TEST_LON = -74.0060
CONNECTION_TEST_RADIUS_NM = 50

SPOOFING_LOCATIONS = (
    {"name": "Ben Gurion Airport", "lat": 32.01, "lon": 34.88},
    {"name": "Beirut Airport", "lat": 33.82, "lon": 35.48},
)
SPOOFING_COORD_TOLERANCE = 0.02
CRUISE_ALTITUDE_FT = 10_000
STALE_POSITION_SECONDS = 20
ISRAEL_REGION_BUFFER_KM = 50

_BASE_REGIONS: dict[str, dict[str, float]] = {
    "Israel - Galilee & Golan (Far North)": {
        "lat_min": 32.8, "lat_max": 33.3, "lon_min": 35.0, "lon_max": 35.9
    },
    "Israel - Haifa & Northern Coast": {
        "lat_min": 32.4, "lat_max": 32.8, "lon_min": 34.8, "lon_max": 35.3
    },
    "Israel - Tel Aviv & Central District": {
        "lat_min": 31.9, "lat_max": 32.4, "lon_min": 34.6, "lon_max": 35.1
    },
    "Israel - Jerusalem & Judea": {
        "lat_min": 31.6, "lat_max": 31.9, "lon_min": 34.9, "lon_max": 35.6
    },
    "Israel - Northern Negev & Beer Sheva": {
        "lat_min": 31.0, "lat_max": 31.6, "lon_min": 34.3, "lon_max": 35.4
    },
    "Israel - Central Negev": {
        "lat_min": 30.3, "lat_max": 31.0, "lon_min": 34.3, "lon_max": 35.2
    },
    "Israel - Eilat & Southern Arava (Far South)": {
        "lat_min": 29.5, "lat_max": 30.3, "lon_min": 34.7, "lon_max": 35.1
    },
    "Jordan": {"lat_min": 29.1, "lat_max": 33.4, "lon_min": 34.9, "lon_max": 39.3},
    "Iraq": {"lat_min": 29.1, "lat_max": 37.4, "lon_min": 38.8, "lon_max": 48.6},
    "Saudi Arabia": {"lat_min": 16.4, "lat_max": 32.2, "lon_min": 34.5, "lon_max": 55.7},
    "Kuwait": {"lat_min": 28.5, "lat_max": 30.1, "lon_min": 46.5, "lon_max": 48.5},
    "United Arab Emirates (UAE)": {
        "lat_min": 22.5, "lat_max": 26.1, "lon_min": 51.5, "lon_max": 56.5
    },
}


def _expand_bounds(bounds: dict[str, float], buffer_km: float) -> dict[str, float]:
    """Expand a rectangular region outward by buffer_km on all sides."""
    lat_mid = (bounds["lat_min"] + bounds["lat_max"]) / 2
    lat_delta = buffer_km / 111.0
    lon_delta = buffer_km / (111.0 * math.cos(math.radians(lat_mid)))
    return {
        "lat_min": bounds["lat_min"] - lat_delta,
        "lat_max": bounds["lat_max"] + lat_delta,
        "lon_min": bounds["lon_min"] - lon_delta,
        "lon_max": bounds["lon_max"] + lon_delta,
    }


def _build_regions() -> dict[str, dict[str, float]]:
    regions: dict[str, dict[str, float]] = {}
    for name, bounds in _BASE_REGIONS.items():
        if name.startswith("Israel - "):
            regions[name] = _expand_bounds(bounds, ISRAEL_REGION_BUFFER_KM)
        else:
            regions[name] = bounds
    return regions


REGIONS: dict[str, dict[str, float]] = _build_regions()

_scheduler: BackgroundScheduler | None = None
_last_request_time = 0.0


@dataclass
class RegionAnalysis:
    region: str
    total_planes: int
    affected_planes: int
    spoofed_count: int
    jammed_count: int
    gps_index: int | None
    disruption_type: str
    has_live_data: bool
    status_label: str


def _respect_rate_limit() -> None:
    """Enforce airplanes.live public API rate limit of ~1 request/second."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < API_RATE_LIMIT_SECONDS:
        time.sleep(API_RATE_LIMIT_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def _build_point_url(lat: float, lon: float, radius_nm: int) -> str:
    """Build the airplanes.live v2 point endpoint URL."""
    radius = min(MAX_RADIUS_NM, max(1, int(radius_nm)))
    return f"{API_BASE_URL}/point/{lat}/{lon}/{radius}"


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(a))


def _bounds_to_point_query(bounds: dict[str, float]) -> tuple[float, float, int]:
    """Derive center point and radius (nm) that covers a rectangular region."""
    lat_center = (bounds["lat_min"] + bounds["lat_max"]) / 2
    lon_center = (bounds["lon_min"] + bounds["lon_max"]) / 2
    corners = (
        (bounds["lat_min"], bounds["lon_min"]),
        (bounds["lat_min"], bounds["lon_max"]),
        (bounds["lat_max"], bounds["lon_min"]),
        (bounds["lat_max"], bounds["lon_max"]),
    )
    max_dist = max(
        _haversine_nm(lat_center, lon_center, lat, lon) for lat, lon in corners
    )
    radius = min(MAX_RADIUS_NM, max(1, int(math.ceil(max_dist))))
    if max_dist > MAX_RADIUS_NM:
        logger.warning(
            "Region bounds exceed %s nm API limit; using max radius (%.4f, %.4f).",
            MAX_RADIUS_NM,
            lat_center,
            lon_center,
        )
    return lat_center, lon_center, radius


def _extract_aircraft_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    aircraft = payload.get("ac")
    if aircraft is not None:
        return aircraft
    aircraft = payload.get("aircraft")
    if aircraft is not None:
        return aircraft
    return []


def _filter_aircraft_in_bounds(
    aircraft: list[dict[str, Any]],
    bounds: dict[str, float],
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for ac in aircraft:
        lat, lon = _extract_position(ac)
        if lat is not None and lon is not None and _in_bounds(lat, lon, bounds):
            filtered.append(ac)
    return filtered


def _in_bounds(lat: float, lon: float, bounds: dict[str, float]) -> bool:
    return (
        bounds["lat_min"] <= lat <= bounds["lat_max"]
        and bounds["lon_min"] <= lon <= bounds["lon_max"]
    )


def _extract_position(aircraft: dict[str, Any]) -> tuple[float | None, float | None]:
    lat = aircraft.get("lat")
    lon = aircraft.get("lon")
    if lat is not None and lon is not None:
        return float(lat), float(lon)

    last_position = aircraft.get("lastPosition") or {}
    lat = last_position.get("lat")
    lon = last_position.get("lon")
    if lat is not None and lon is not None:
        return float(lat), float(lon)

    rr_lat = aircraft.get("rr_lat")
    rr_lon = aircraft.get("rr_lon")
    if rr_lat is not None and rr_lon is not None:
        return float(rr_lat), float(rr_lon)

    return None, None


def _altitude_ft(aircraft: dict[str, Any]) -> float | None:
    alt = aircraft.get("alt_baro")
    if alt in (None, "ground"):
        return None
    try:
        return float(alt)
    except (TypeError, ValueError):
        return None


def _ground_speed_knots(aircraft: dict[str, Any]) -> float | None:
    gs = aircraft.get("gs")
    if gs is None:
        return None
    try:
        return float(gs)
    except (TypeError, ValueError):
        return None


def _is_spoofed(aircraft: dict[str, Any]) -> bool:
    alt = _altitude_ft(aircraft)
    gs = _ground_speed_knots(aircraft)
    lat, lon = _extract_position(aircraft)

    if alt is not None and alt > CRUISE_ALTITUDE_FT and (gs is None or gs == 0):
        return True

    if lat is not None and lon is not None and alt is not None and alt > CRUISE_ALTITUDE_FT:
        for location in SPOOFING_LOCATIONS:
            if (
                abs(lat - location["lat"]) <= SPOOFING_COORD_TOLERANCE
                and abs(lon - location["lon"]) <= SPOOFING_COORD_TOLERANCE
            ):
                return True

    return False


def _has_primary_position(aircraft: dict[str, Any]) -> bool:
    return aircraft.get("lat") is not None and aircraft.get("lon") is not None


def _is_jammed(aircraft: dict[str, Any]) -> bool:
    last_position = aircraft.get("lastPosition") or {}
    has_primary = _has_primary_position(aircraft)
    has_last = (
        last_position.get("lat") is not None
        and last_position.get("lon") is not None
    )

    if not has_primary and not has_last:
        return True

    if aircraft.get("gpsOkBefore") is not None:
        return True

    if not has_primary and (
        aircraft.get("rr_lat") is not None or aircraft.get("rr_lon") is not None
    ):
        return True

    seen_pos = aircraft.get("seen_pos")
    if seen_pos is not None:
        try:
            if float(seen_pos) > STALE_POSITION_SECONDS:
                return True
        except (TypeError, ValueError):
            return True

    return False


def _determine_disruption_type(spoofed_count: int, jammed_count: int) -> str:
    if spoofed_count > 0 and jammed_count > 0:
        return "Spoofing" if spoofed_count >= jammed_count else "Jamming"
    if spoofed_count > 0:
        return "Spoofing"
    if jammed_count > 0:
        return "Jamming"
    return "None"


def _fetch_point(lat: float, lon: float, radius_nm: int) -> dict[str, Any]:
    """Fetch aircraft via keyless https://api.airplanes.live/v2/point/... GET."""
    url = _build_point_url(lat, lon, radius_nm)
    _respect_rate_limit()
    response = requests.get(url, timeout=API_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def fetch_region_aircraft(bounds: dict[str, float]) -> list[dict[str, Any]]:
    """Fetch live aircraft for a region using keyless point/radius queries."""
    lat, lon, radius = _bounds_to_point_query(bounds)
    try:
        payload = _fetch_point(lat, lon, radius)
        return _filter_aircraft_in_bounds(_extract_aircraft_list(payload), bounds)
    except (requests.RequestException, ValueError) as exc:
        logger.error(
            "Point API request failed for bounds %s (%.4f, %.4f, %d nm): %s",
            bounds,
            lat,
            lon,
            radius,
            exc,
        )
        return []


def test_api_connection() -> bool:
    """
    Verify keyless airplanes.live access with a NYC probe request.

    Hits /v2/point/40.7128/-74.0060/50, checks HTTP 200, and logs the
    top-level aircraft count from the JSON payload.
    """
    url = _build_point_url(
        CONNECTION_TEST_LAT, CONNECTION_TEST_LON, CONNECTION_TEST_RADIUS_NM
    )
    try:
        _respect_rate_limit()
        response = requests.get(url, timeout=API_TIMEOUT_SECONDS)
        if response.status_code != 200:
            logger.error(
                "airplanes.live connection test failed: HTTP %s for %s",
                response.status_code,
                url,
            )
            return False

        payload = response.json()
        count = payload.get("total")
        if count is None:
            aircraft = payload.get("ac")
            count = len(aircraft) if aircraft is not None else "unknown"

        message = f"airplanes.live connection test OK: HTTP 200, aircraft count={count}"
        logger.info(message)
        print(message)
        return True
    except (requests.RequestException, ValueError) as exc:
        logger.error("airplanes.live connection test failed: %s", exc)
        return False


def analyze_region(region_name: str, bounds: dict[str, float]) -> RegionAnalysis:
    """Analyze one region and compute its GPS disruption index."""
    aircraft = fetch_region_aircraft(bounds)
    total_planes = len(aircraft)

    if total_planes == 0:
        return RegionAnalysis(
            region=region_name,
            total_planes=0,
            affected_planes=0,
            spoofed_count=0,
            jammed_count=0,
            gps_index=None,
            disruption_type="None",
            has_live_data=False,
            status_label="No Data Available",
        )

    spoofed_count = 0
    jammed_count = 0
    affected_hexes: set[str] = set()

    for ac in aircraft:
        hex_id = ac.get("hex", id(ac))
        is_spoofed = _is_spoofed(ac)
        is_jammed = _is_jammed(ac)

        if is_spoofed:
            spoofed_count += 1
            affected_hexes.add(str(hex_id))
        if is_jammed:
            jammed_count += 1
            affected_hexes.add(str(hex_id))

    affected_planes = len(affected_hexes)
    raw_index = ((spoofed_count + jammed_count) / total_planes) * 100
    gps_index = min(100, int(round(raw_index)))
    disruption_type = _determine_disruption_type(spoofed_count, jammed_count)

    return RegionAnalysis(
        region=region_name,
        total_planes=total_planes,
        affected_planes=affected_planes,
        spoofed_count=spoofed_count,
        jammed_count=jammed_count,
        gps_index=gps_index,
        disruption_type=disruption_type,
        has_live_data=True,
        status_label="Live",
    )


def run_collection_cycle() -> tuple[datetime | None, list[RegionAnalysis]]:
    """Fetch and analyze all regions. Returns timestamp and analysis results."""
    timestamp = datetime.now()
    results: list[RegionAnalysis] = []
    readings_to_store: list[dict[str, Any]] = []
    any_success = False

    for region_name, bounds in REGIONS.items():
        try:
            analysis = analyze_region(region_name, bounds)
            results.append(analysis)
            any_success = True

            if analysis.has_live_data and analysis.gps_index is not None:
                readings_to_store.append(
                    {
                        "timestamp": timestamp,
                        "region": analysis.region,
                        "gps_index": analysis.gps_index,
                        "disruption_type": analysis.disruption_type,
                        "affected_planes": analysis.affected_planes,
                        "total_planes": analysis.total_planes,
                    }
                )
        except Exception as exc:
            logger.exception("Failed to analyze region %s: %s", region_name, exc)
            results.append(
                RegionAnalysis(
                    region=region_name,
                    total_planes=0,
                    affected_planes=0,
                    spoofed_count=0,
                    jammed_count=0,
                    gps_index=None,
                    disruption_type="None",
                    has_live_data=False,
                    status_label="No Data Available",
                )
            )

    if any_success:
        if readings_to_store:
            database.insert_readings_batch(readings_to_store)
        database.set_last_collection_time(timestamp)
        return timestamp, results

    return None, results


def _reading_to_analysis(reading: dict[str, Any]) -> RegionAnalysis:
    return RegionAnalysis(
        region=reading["region"],
        total_planes=reading["total_planes"],
        affected_planes=reading["affected_planes"],
        spoofed_count=0,
        jammed_count=0,
        gps_index=reading["gps_index"],
        disruption_type=reading["disruption_type"],
        has_live_data=True,
        status_label="Live",
    )


def get_dashboard_snapshot() -> tuple[str | None, list[RegionAnalysis]]:
    """Build dashboard view from the latest persisted collection cycle."""
    last_collection, cycle_readings = database.get_latest_cycle_readings()
    reading_map = {row["region"]: row for row in cycle_readings}
    results: list[RegionAnalysis] = []

    for region_name in REGIONS:
        reading = reading_map.get(region_name)
        if reading:
            results.append(_reading_to_analysis(reading))
        else:
            results.append(
                RegionAnalysis(
                    region=region_name,
                    total_planes=0,
                    affected_planes=0,
                    spoofed_count=0,
                    jammed_count=0,
                    gps_index=None,
                    disruption_type="None",
                    has_live_data=False,
                    status_label="No Data Available",
                )
            )

    return last_collection, results


def get_live_dashboard_data() -> tuple[str | None, list[RegionAnalysis]]:
    """Run a fresh analysis cycle for on-demand UI refresh."""
    timestamp, results = run_collection_cycle()
    last_collection = (
        timestamp.strftime("%Y-%m-%d %H:%M:%S")
        if timestamp
        else database.get_last_collection_time()
    )
    return last_collection, results


def start_background_scheduler() -> BackgroundScheduler:
    """Start the 2-minute background polling loop."""
    global _scheduler

    if _scheduler and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        run_collection_cycle,
        trigger="interval",
        minutes=POLL_INTERVAL_MINUTES,
        id="gps_poll",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("Background scheduler started (every %s minutes).", POLL_INTERVAL_MINUTES)

    test_api_connection()

    try:
        run_collection_cycle()
    except Exception as exc:
        logger.exception("Initial collection cycle failed: %s", exc)

    return _scheduler


def shutdown_background_scheduler() -> None:
    """Stop the background scheduler if running."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
