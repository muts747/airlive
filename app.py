"""Streamlit UI for Middle East GPS disruption tracking."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

import bootstrap
import database
import data_engine

POLL_INTERVAL = timedelta(minutes=data_engine.POLL_INTERVAL_MINUTES)
HISTORY_VISIBLE_ROWS = 10
HISTORY_ROW_HEIGHT_REM = 2.75

st.set_page_config(
    page_title="Middle East GPS Disruption Tracker",
    page_icon="📡",
    layout="wide",
)

CUSTOM_CSS = f"""
<style>
    .landing-title {{
        text-align: center;
        font-size: 2.4rem;
        font-weight: 700;
        margin-top: 2rem;
        margin-bottom: 0.5rem;
    }}
    .landing-subtitle {{
        text-align: center;
        color: #6b7280;
        margin-bottom: 2rem;
    }}
    .meta-header {{
        background: #111827;
        color: #f9fafb;
        padding: 0.9rem 1.2rem;
        border-radius: 0.5rem;
        font-size: 1.05rem;
        margin-bottom: 1rem;
    }}
    .index-green {{
        background-color: #d1fae5;
        color: #065f46;
        font-weight: 700;
        padding: 0.35rem 0.6rem;
        border-radius: 0.35rem;
        display: inline-block;
    }}
    .index-yellow {{
        background-color: #fef3c7;
        color: #92400e;
        font-weight: 700;
        padding: 0.35rem 0.6rem;
        border-radius: 0.35rem;
        display: inline-block;
    }}
    .index-red {{
        background-color: #fee2e2;
        color: #991b1b;
        font-weight: 700;
        padding: 0.35rem 0.6rem;
        border-radius: 0.35rem;
        display: inline-block;
    }}
    .history-scroll {{
        height: calc({HISTORY_ROW_HEIGHT_REM}rem * {HISTORY_VISIBLE_ROWS + 1});
        overflow-y: auto;
        border: 1px solid #e5e7eb;
        border-radius: 0.5rem;
        padding: 0.5rem;
        background: #fafafa;
    }}
    .history-scroll table {{
        width: 100%;
        border-collapse: collapse;
    }}
    .history-scroll thead tr,
    .history-scroll tbody tr {{
        height: {HISTORY_ROW_HEIGHT_REM}rem;
    }}
</style>
"""


@st.cache_resource
def _ensure_backend_started() -> bool:
    bootstrap.ensure_backend_started()
    return True


def _index_color_class(value: int) -> str:
    if value == 0 or 1 <= value <= 20:
        return "index-green"
    if 21 <= value <= 50:
        return "index-yellow"
    return "index-red"


def _format_index_cell(value: int | None) -> str:
    if value is None:
        return "N/A"
    css_class = _index_color_class(value)
    return f'<span class="{css_class}">{value}</span>'


def _build_summary_rows(
    live_results: list[data_engine.RegionAnalysis],
    latest_history: dict[str, dict],
) -> list[dict[str, str]]:
    rows = []
    for result in live_results:
        if result.has_live_data and result.gps_index is not None:
            index_display = _format_index_cell(result.gps_index)
            disruption = result.disruption_type
            if disruption == "Jamming":
                disruption = "Jammed"
            elif disruption == "Spoofing":
                disruption = "Spoofed"
            aircraft_count = f"{result.affected_planes} / {result.total_planes}"
            last_known = "—"
        else:
            index_display = "N/A"
            disruption = "None"
            aircraft_count = "0 / 0"
            hist = latest_history.get(result.region)
            if hist:
                last_known = (
                    f"Index: {hist['gps_index']} ({hist['timestamp'][:16]})"
                )
            else:
                last_known = "No historical data"

        rows.append(
            {
                "Region": result.region,
                "Current GPS Disruption Index (1-100)": index_display,
                "Disruption Type": disruption,
                "Aircraft Count": aircraft_count,
                "Last Known Status": last_known,
                "_has_live_data": result.has_live_data,
            }
        )
    return rows


def _render_html_table(df: pd.DataFrame) -> None:
    html = df.to_html(escape=False, index=False)
    st.markdown(html, unsafe_allow_html=True)


@st.fragment(run_every=POLL_INTERVAL)
def _render_dashboard() -> None:
    """Dashboard body; auto-refreshes every 2 minutes with the background poll cycle."""
    st.title("GPS Disruption Dashboard")

    last_collection = database.get_last_collection_time()
    if last_collection:
        st.markdown(
            f'<div class="meta-header">'
            f"<strong>Last Successful Data Collection:</strong> {last_collection}"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="meta-header">'
            "<strong>Last Successful Data Collection:</strong> "
            "Awaiting first collection cycle..."
            "</div>",
            unsafe_allow_html=True,
        )

    col_filter, col_clear = st.columns([3, 1])
    with col_filter:
        filter_no_data = st.checkbox("Filter out regions with no data", value=False)
    with col_clear:
        if st.button("Clear All History", type="secondary"):
            database.clear_all_history()
            st.success("All historical tracking data has been cleared.")
            st.rerun()

    _, live_results = data_engine.get_dashboard_snapshot()
    latest_history = database.get_latest_readings_per_region()
    summary_rows = _build_summary_rows(live_results, latest_history)

    if filter_no_data:
        summary_rows = [row for row in summary_rows if row["_has_live_data"]]

    display_df = pd.DataFrame(summary_rows).drop(columns=["_has_live_data"])
    st.subheader("Regional Summary")
    _render_html_table(display_df)

    st.subheader("Historical Timeline (Past 24 Hours)")
    history = database.get_history_last_24_hours()

    if not history:
        st.info("No historical readings recorded yet. Data is collected every 2 minutes.")
    else:
        history_df = pd.DataFrame(history)
        history_df = history_df.rename(
            columns={
                "timestamp": "Timestamp",
                "region": "Region",
                "gps_index": "GPS Index",
                "disruption_type": "Disruption Type",
                "affected_planes": "Affected",
                "total_planes": "Total",
            }
        )
        history_df["Aircraft Count"] = (
            history_df["Affected"].astype(str) + " / " + history_df["Total"].astype(str)
        )
        history_df = history_df[
            [
                "Timestamp",
                "Region",
                "GPS Index",
                "Disruption Type",
                "Aircraft Count",
            ]
        ]

        st.markdown('<div class="history-scroll">', unsafe_allow_html=True)
        _render_html_table(history_df)
        st.markdown("</div>", unsafe_allow_html=True)
        st.caption(
            f"Scroll up inside the panel above to view older readings. "
            f"The {HISTORY_VISIBLE_ROWS} most recent entries are visible without scrolling."
        )


def main() -> None:
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    _ensure_backend_started()

    if "show_dashboard" not in st.session_state:
        st.session_state.show_dashboard = False

    if not st.session_state.show_dashboard:
        st.markdown(
            '<div class="landing-title">Middle East GPS Disruption Tracker</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="landing-subtitle">'
            "Real-time and historical jamming &amp; spoofing monitoring "
            "across 12 geofenced regions"
            "</div>",
            unsafe_allow_html=True,
        )
        _, center_col, _ = st.columns([1, 1, 1])
        with center_col:
            if st.button("Show Dashboard", type="primary", use_container_width=True):
                st.session_state.show_dashboard = True
                st.rerun()
        return

    _render_dashboard()


if __name__ == "__main__":
    main()
