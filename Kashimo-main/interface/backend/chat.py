"""
backend/chat.py
Operator chat — free-form Q&A with the same Claude model that powers Brain 2.
Each turn re-reads the latest tick from data/live_log.csv so answers are grounded
in live grid state, not a stale snapshot.

Public API:
    from backend.chat import chat
    reply = chat(messages)   # messages = [{"role": "user"|"assistant", "content": str}, ...]
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import anthropic
import pandas as pd

from .grid_connection import get_connection_status, get_history, get_latest_state

# ── Load .env from project root (mirrors brain2.py's pattern) ─────────────────
_ENV_PATH = Path(__file__).parent.parent.parent / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

MODEL      = "claude-sonnet-4-6"
MAX_TOKENS = 1024

# ── Static system prompt ──────────────────────────────────────────────────────
# Cached so repeated operator questions inside one tick don't re-charge for the
# topology + role tokens. Refreshes every 5 min (Anthropic ephemeral TTL).
_SYSTEM_BASE = """You are the operator-facing AI assistant for GridAgent — a live grid-monitoring
system for the Pepco DC distribution network in Washington, DC. Your job is to
answer the human grid operator's questions in plain English about what is
happening on the grid right now, why the automated agent (Brain 2) made the
recommendations it did, and what actions are available.

# Grid topology (Pepco DC)
- 7 transmission substations at 115 kV, 51 distribution substations at 13.8 kV.
- 10 transmission lines forming a meshed ring around DC. Busiest pairs are
  Benning↔Georgetown and Nevada↔Benning.
- 3 generators: Benning Road (slack, ~1500 MW capacity), Georgetown (~300 MW),
  Buzzard Point (~250 MW). Total dispatchable capacity ≈ 2,050 MW.
- 6 distributed renewable units (~70 MW total, mostly rooftop solar in NE/SE/NW).
- Static city load peaks around 1,182 MW. Daily curve runs 0.77×–1.16× of that.
- Flexible load: DC_NoMa data center hub at NoMa station. Baseline 110 MW,
  hard limits 60–180 MW, 25% of baseline is deferrable (~27.5 MW).

# Brain pipeline
- Brain 1 (numerical): scores risk on three axes every tick. Each axis 0–1.
    * Line risk: 0.10 < 75% loading, 0.35 ≥ 75%, 0.65 ≥ 85%, 0.90 ≥ 90% (CRITICAL).
    * Voltage risk: 0.05 in [0.97, 1.03] pu, 0.40 if ≤ 0.97, 0.90 if ≤ 0.95 or ≥ 1.05.
    * Reserve risk: 0.10 > 400 MW, 0.50 ≥ 300 MW, 0.90 ≤ 300 MW.
  Overall risk = max of the three. action_needed when overall ≥ 0.65.
- Brain 2 (LLM, that's you in another mode): when action_needed, picks ONE of:
    defer_workload    — postpone deferrable DC_NoMa jobs (~16 MW relief)
    curtail_load      — reduce DC_NoMa 20% below baseline now (immediate relief)
    restore_baseline  — return DC_NoMa to 110 MW (after stress passes)
    alert_operator    — too complex for auto-action, escalate to human
    no_action         — risk below threshold

# Event types the data center can throw at the grid
- ai_training_spike: training job launches, +25–60 MW step (random magnitude).
- ai_training_dropout: job crashes, load drops 75% — voltage rises sharply.
- cooling_cascade: compute spike +40 MW, then cooling kicks in 30 min later +18 MW.
- load_oscillation: power-electronics hunting, ±15 MW sinusoid every 60 min.

# How to answer
- Operators value brevity. Default to 2–4 sentences.
- Reference concrete numbers from the snapshot (MW, %, pu, tick) — don't speak
  abstractly. If the snapshot says reserve is 312 MW, say "312 MW," not "low".
- If the operator asks "why this action," explain the trigger condition that fired
  AND name the lever Brain 2 would pull (defer vs curtail vs restore).
- If the data needed isn't in the snapshot, say so — don't invent numbers.
- If the grid is OFFLINE (no fresh ticks), tell the operator to start
  `python src/run_live.py` and answer from the most recent recorded state.
- You are advising, not executing. The operator decides; you explain.
"""


# ── Snapshot builder ──────────────────────────────────────────────────────────

def _b1_line_score(pct: float) -> float:
    if pct >= 90: return 0.90
    if pct >= 85: return 0.65
    if pct >= 75: return 0.35
    return 0.10


def _b1_voltage_score(v: float) -> float:
    if v <= 0.95 or v >= 1.05: return 0.90
    if v <= 0.97 or v >= 1.03: return 0.40
    return 0.05


def _b1_reserve_score(mw: float) -> float:
    if mw <= 300: return 0.90
    if mw <= 400: return 0.50
    return 0.10


def _format_snapshot(state: dict | None, history: pd.DataFrame, conn: dict) -> str:
    """Render the live grid state into a compact text block for the model."""
    if state is None:
        return (
            f"# Live grid snapshot\n"
            f"CONNECTION: {conn['status']}\n"
            f"{conn['message']}\n\n"
            f"No tick data is available. The operator should start the live simulation.\n"
        )

    tick      = int(state.get("tick", 0))
    sim_time  = state.get("sim_time", "??:??")
    day       = int(state.get("day", 1))
    mult      = float(state.get("load_multiplier", 1.0))
    load_mw   = float(state.get("total_load_mw", 0.0))
    gen_mw    = float(state.get("total_gen_mw", 0.0))
    sgen_mw   = float(state.get("total_sgen_mw", 0.0))
    reserve   = float(state.get("reserve_mw", 0.0))
    max_line  = float(state.get("max_line_loading_pct", 0.0))
    v_min     = float(state.get("min_voltage_pu", 1.0))
    dc_mw     = float(state.get("dc_noma_mw", 110.0))
    risk      = float(state.get("brain1_risk", 0.0))
    n_viol    = int(state.get("n_violations", 0))
    converged = bool(int(state.get("converged", 1)))

    b2_action = str(state.get("brain2_action", "") or "—")
    b2_target = str(state.get("brain2_target", "") or "—")
    b2_conf   = str(state.get("brain2_confidence", "") or "—")
    b2_fired  = bool(int(state.get("brain2_triggered", 0)))

    ev_fired  = bool(int(state.get("event_fired", 0)))
    ev_name   = str(state.get("event_name", "") or "")
    ev_type   = str(state.get("event_type", "") or "")
    op_choice = str(state.get("operator_choice", "") or "")
    active    = str(state.get("active_events", "") or "")

    line_score    = _b1_line_score(max_line)
    voltage_score = _b1_voltage_score(v_min)
    reserve_score = _b1_reserve_score(reserve)

    # Mini-trend over last ~5 ticks (≈2.5 simulated hours at 30-min ticks)
    trend = ""
    if not history.empty:
        recent = history.tail(6)
        rows = []
        for _, r in recent.iterrows():
            rows.append(
                f"  T{int(r['tick']):>4}  {str(r.get('sim_time','')):>5}  "
                f"load {float(r.get('total_load_mw',0)):>6.0f} MW  "
                f"reserve {float(r.get('reserve_mw',0)):>5.0f} MW  "
                f"line {float(r.get('max_line_loading_pct',0)):>5.1f}%  "
                f"risk {float(r.get('brain1_risk',0)):.2f}"
            )
        trend = "Recent ticks (oldest first):\n" + "\n".join(rows)

    # Recent Brain 2 interventions over last ~144 ticks
    interventions = ""
    if not history.empty and "brain2_triggered" in history.columns:
        agent_rows = history[history["brain2_triggered"] == 1].tail(5)
        if not agent_rows.empty:
            ilines = []
            for _, r in agent_rows.iterrows():
                ilines.append(
                    f"  T{int(r['tick']):>4} {str(r.get('sim_time','')):>5}  "
                    f"{str(r.get('brain2_action',''))} → {str(r.get('brain2_target',''))}  "
                    f"(risk {float(r.get('brain1_risk',0)):.2f}, conf {str(r.get('brain2_confidence',''))})"
                )
            interventions = "Last Brain 2 interventions:\n" + "\n".join(ilines)

    # Recent fired events
    events_section = ""
    if not history.empty and "event_fired" in history.columns:
        ev_rows = history[history["event_fired"] == 1].tail(5)
        if not ev_rows.empty:
            elines = []
            for _, r in ev_rows.iterrows():
                op = str(r.get("operator_choice", "") or "")
                op_str = f" (operator: {op})" if op else " (no operator response)"
                elines.append(
                    f"  T{int(r['tick']):>4} {str(r.get('sim_time','')):>5}  "
                    f"{str(r.get('event_type',''))} on {str(r.get('event_name',''))}{op_str}"
                )
            events_section = "Recent events:\n" + "\n".join(elines)

    return f"""# Live grid snapshot — {conn['status']}
{conn['message']}

## Current tick
Day {day}  ·  sim time {sim_time}  ·  tick {tick}  ·  load multiplier ×{mult:.4f}
Power flow converged: {converged}

## KPIs
- Total demand: {load_mw:.1f} MW  (city + data centers)
- Generation: {gen_mw:.1f} MW (slack+conventional) + {sgen_mw:.1f} MW (renewables)
- Reserve margin (capacity headroom): {reserve:.1f} MW
- Busiest transmission line loading: {max_line:.1f}%
- Min bus voltage: {v_min:.4f} pu
- DC_NoMa data center draw: {dc_mw:.1f} MW (baseline 110 MW)
- Limit violations active: {n_viol}

## Brain 1 risk (this tick)
- Overall: {risk:.3f}   →   action_needed = {risk >= 0.65}
- Line score:    {line_score:.2f}  (from {max_line:.1f}% loading)
- Voltage score: {voltage_score:.2f}  (from {v_min:.4f} pu)
- Reserve score: {reserve_score:.2f}  (from {reserve:.0f} MW)

## Brain 2 (this tick)
- Triggered this tick: {b2_fired}
- Recommended action: {b2_action}  →  target: {b2_target}  (confidence: {b2_conf})

## Event state
- Event fired this tick: {ev_fired}{f" — {ev_type} ({ev_name})" if ev_fired else ""}
- Active events: {active or "none"}
- Operator response logged this tick: {op_choice or "none"}

## History
{trend or "  (no history yet)"}

{interventions}

{events_section}
""".strip()


# ── Public entry point ────────────────────────────────────────────────────────

def chat(messages: List[Dict[str, str]]) -> str:
    """
    Run one operator chat turn.

    Args:
        messages: list of {"role": "user"|"assistant", "content": str}.
                  The most recent entry should be the user's new question.

    Returns:
        Assistant reply text.
    """
    if not messages or messages[-1].get("role") != "user":
        raise ValueError("chat() requires a non-empty messages list ending in a user turn.")

    conn    = get_connection_status()
    state   = get_latest_state()
    history = get_history(n=144)   # ~1 simulated day

    snapshot = _format_snapshot(state, history, conn)

    system_blocks = [
        {
            "type": "text",
            "text": _SYSTEM_BASE,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": snapshot,
        },
    ]

    response = _client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_blocks,
        messages=messages,
    )

    if not response.content:
        return "(no response from model)"
    return response.content[0].text.strip()
