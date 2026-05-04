"""
backend/grid_connection.py
Detects whether run_live.py has an active grid instance and exposes
the live log data for the frontend to consume.

Detection is file-based: run_live.py flushes a UTC timestamp to
data/live_log.csv after every tick.  Three states are possible:

  LIVE    — last tick written < 15 s ago  (simulation running normally)
  PAUSED  — last tick 15–360 s ago        (operator prompt; can block up to 300 s)
  OFFLINE — last tick > 360 s ago, or file missing / empty
"""

from __future__ import annotations
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT                = Path(__file__).parent.parent.parent   # project root
LIVE_LOG_PATH        = _ROOT / "data" / "live_log.csv"
OPERATOR_RESP_PATH   = _ROOT / "data" / "operator_response.json"

# ── Staleness thresholds (seconds since last CSV write) ────────────────────────
_LIVE_S   = 15    # within 15 s  → LIVE
_PAUSED_S = 360   # within 360 s → PAUSED  (operator timeout is 300 s + 60 s buffer)

# Columns written by run_live.py — used to validate the file on open
EXPECTED_COLUMNS = [
    "timestamp_utc", "tick", "sim_time", "day", "day_tick",
    "load_multiplier", "total_load_mw", "total_gen_mw", "total_sgen_mw",
    "dc_noma_mw",
    "reserve_mw", "max_line_loading_pct", "min_voltage_pu",
    "n_violations", "converged",
    "brain1_risk", "action_needed",
    "brain2_triggered", "brain2_action", "brain2_target", "brain2_confidence",
    "event_fired", "event_name", "event_type",
    "operator_choice", "active_events",
]


# ── Public API ────────────────────────────────────────────────────────────────

def get_connection_status() -> dict:
    """
    Check whether run_live.py is actively writing the live log.

    Returns:
        {
          "status":     "LIVE" | "PAUSED" | "OFFLINE",
          "message":    str,      # human-readable for the dashboard banner
          "age_s":      float | None,   # seconds since last tick was written
          "last_tick":  int | None,
          "total_ticks": int | None,    # total rows in the log
        }
    """
    if not LIVE_LOG_PATH.exists() or LIVE_LOG_PATH.stat().st_size == 0:
        return {
            "status":      "OFFLINE",
            "message":     "No grid connected — run  python src/run_live.py  to start.",
            "age_s":       None,
            "last_tick":   None,
            "total_ticks": None,
        }

    try:
        df = pd.read_csv(LIVE_LOG_PATH, usecols=["timestamp_utc", "tick"])
    except Exception as exc:
        return {
            "status":      "OFFLINE",
            "message":     f"Could not read live log: {exc}",
            "age_s":       None,
            "last_tick":   None,
            "total_ticks": None,
        }

    if df.empty:
        return {
            "status":      "OFFLINE",
            "message":     "Live log exists but contains no data yet.",
            "age_s":       None,
            "last_tick":   None,
            "total_ticks": 0,
        }

    last_row  = df.iloc[-1]
    last_tick = int(last_row["tick"])
    total     = len(df)

    try:
        last_ts = datetime.fromisoformat(last_row["timestamp_utc"])
        age_s   = (datetime.now(timezone.utc) - last_ts).total_seconds()
    except Exception:
        # Fall back to file mtime if the timestamp can't be parsed
        age_s = time.time() - LIVE_LOG_PATH.stat().st_mtime

    if age_s < _LIVE_S:
        status  = "LIVE"
        message = f"Grid connected  ·  tick {last_tick}  ·  {total} ticks recorded"
    elif age_s < _PAUSED_S:
        status  = "PAUSED"
        message = (
            f"Grid paused — awaiting operator input  "
            f"·  last tick {last_tick}  ·  {age_s:.0f} s ago"
        )
    else:
        mins = age_s / 60
        status  = "OFFLINE"
        message = (
            f"Grid offline  ·  last activity {mins:.1f} min ago  "
            f"(tick {last_tick})"
        )

    return {
        "status":      status,
        "message":     message,
        "age_s":       round(age_s, 1),
        "last_tick":   last_tick,
        "total_ticks": total,
    }


def get_history(n: int = 500) -> pd.DataFrame:
    """
    Return the last *n* rows of the live log as a DataFrame.
    Returns an empty DataFrame if the file is missing or unreadable.
    """
    if not LIVE_LOG_PATH.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(LIVE_LOG_PATH)
        return (df if n == 0 else df.tail(n)).reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def get_latest_state() -> dict | None:
    """
    Return the most recent tick's full row as a plain dict.
    Returns None if the log is missing, empty, or unreadable.
    """
    df = get_history(n=1)
    if df.empty:
        return None
    return df.iloc[-1].to_dict()


def get_events(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Filter a history DataFrame to rows where event_fired == 1.
    If *df* is None, reads the full log.
    Returns an empty DataFrame if there are no events.
    """
    if df is None:
        df = get_history(n=0)   # n=0 → unlimited
    if df.empty or "event_fired" not in df.columns:
        return pd.DataFrame()
    return df[df["event_fired"] == 1].reset_index(drop=True)
