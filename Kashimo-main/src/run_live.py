"""
Live Grid Simulation
====================
Runs the Pepco DC grid continuously, one simulated 10-minute interval per tick.
Load levels follow a 24-hour demand curve (144 ticks/day) that cycles automatically.
When an event fires, the simulation pauses and Brain 2 presents an action menu
to the operator. Whatever choice the operator makes, the event is resolved.
If no response is received within 5 minutes, the event persists.

Stop at any time with Ctrl+C.

Usage:
    python run_live.py                  # 1 tick per second (default)
    python run_live.py --speed 0.25     # fast mode (4 ticks/sec)
    python run_live.py --speed 5        # slow mode (1 tick per 5 sec)
    python run_live.py --ticks 288      # run exactly 2 simulated days then stop
"""

import argparse
import csv
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))         # src/
sys.path.insert(0, str(Path(__file__).parent.parent))  # project root (for simulator/)

from simulator.brain1 import score as brain1
from simulator.brain2 import run as brain2
from simulator.events import EventScheduler, GridEvent, EventType
from simulator.build_simulation import SimulationEnvironment

MINS_PER_TICK  = 30                      # simulated minutes per tick
TICKS_PER_HOUR = 60 // MINS_PER_TICK    # = 2
TICKS_PER_DAY  = 24 * TICKS_PER_HOUR   # = 48

# ── 24-hour hourly load multiplier anchors ─────────────────────────────────────
# Calibrated for 10-min resolution. 1.0 = baseline (~1,182 MW).
# Range 0.77–1.16 reflects real utility zone trough-to-peak ratio (~1.5×).
# Steepest ramp (07:00–09:00) produces ~1.2%/10 min — consistent with PJM DOM data.
# Fastest decay (21:00–23:00) ~1.5%/10 min — load drops quickly at end of business day.
_HOURLY = [
    0.81,  # 00:00 — late night
    0.80,  # 01:00
    0.79,  # 02:00
    0.78,  # 03:00
    0.77,  # 04:00 — overnight trough
    0.78,  # 05:00 — pre-dawn uptick
    0.81,  # 06:00 — morning start    (+0.5%/10 min)
    0.88,  # 07:00 — steep ramp       (+1.2%/10 min)
    0.95,  # 08:00                    (+1.2%/10 min)
    1.00,  # 09:00 — approaches peak  (+0.8%/10 min)
    1.02,  # 10:00 — plateau          (+0.3%/10 min)
    1.03,  # 11:00
    1.03,  # 12:00 — midday plateau
    1.04,  # 13:00
    1.05,  # 14:00
    1.07,  # 15:00 — afternoon build  (+0.3%/10 min)
    1.10,  # 16:00                    (+0.5%/10 min)
    1.13,  # 17:00                    (+0.5%/10 min)
    1.15,  # 18:00                    (+0.3%/10 min)
    1.16,  # 19:00 — evening peak     (+0.2%/10 min)
    1.14,  # 20:00 — begin decay      (-0.3%/10 min)
    1.09,  # 21:00                    (-0.8%/10 min)
    1.00,  # 22:00                    (-1.5%/10 min)
    0.90,  # 23:00 — fast evening drop (-1.5%/10 min → wraps to 00:00 at -1.5%/10 min)
]

# Linearly interpolate hourly anchors to 10-minute resolution (144 entries).
LOAD_PROFILE = []
for _i in range(TICKS_PER_DAY):
    _h    = _i // TICKS_PER_HOUR
    _frac = (_i % TICKS_PER_HOUR) / TICKS_PER_HOUR
    _v0   = _HOURLY[_h]
    _v1   = _HOURLY[(_h + 1) % 24]
    LOAD_PROFILE.append(round(_v0 + _frac * (_v1 - _v0), 4))

# Real-time window the operator has to respond before the simulation resumes.
# If no input is received, the event persists until its natural duration expires.
OPERATOR_TIMEOUT_SECONDS = 300  # 5 minutes

# Scale city loads down from the config's 1,182 MW baseline so the simulation
# starts at ~800 MW total (city + DC) at midnight and peaks around 1,100 MW.
# The config values reflect the real Pepco topology; this factor brings the
# demo to a range where a 110 MW data center is a meaningful fraction of load.
BASE_LOAD_SCALE = 0.72


# ── Daily event pattern ───────────────────────────────────────────────────────
# Four AI data center events fire at the same clock time every day.
# The repeating pattern lets the agent learn to predict the next day's events.
#
#   06:00  AI_TRAINING_SPIKE   — morning job launches, unknown magnitude
#   14:00  AI_TRAINING_DROPOUT — job crashes mid-afternoon, load drops 75%
#   18:00  COOLING_CASCADE     — evening compute surge + 30-min thermal lag
#   21:00  LOAD_OSCILLATION    — power-electronics hunting overnight
#
# Tick offsets within a 144-tick day (1 tick = 10 min):
#   36 = 06:00,  84 = 14:00,  108 = 18:00,  126 = 21:00
#
# cooling_delay=3 → 30-min thermal lag  (realistic for CRAC units)
# period_steps=4  → 40-min oscillation  (realistic for VFD hunting)

_DAILY_PATTERN = [
    # (day_tick_offset, event_type, base_name, duration_steps, params)
    # Offsets and durations in ticks (1 tick = 30 min):
    #   06:00 = tick 12,  14:00 = tick 28,  18:00 = tick 36,  21:00 = tick 42
    #   cooling_delay=1 → 30-min thermal lag
    #   period_steps=2  → 60-min oscillation cycle
    (12, EventType.AI_TRAINING_SPIKE,   "ai_spike",    8,  {"min_mw": 25.0, "max_mw": 60.0}),
    (28, EventType.AI_TRAINING_DROPOUT, "ai_dropout",  6,  {"dropout_pct": 0.75}),
    (36, EventType.COOLING_CASCADE,     "cooling",     12, {"compute_mw": 40.0, "cooling_delay": 1, "cooling_mw": 18.0}),
    (42, EventType.LOAD_OSCILLATION,    "oscillation", 16, {"amplitude_mw": 15.0, "period_steps": 2.0}),
]

_N_DAYS = 7   # pre-schedule this many days; extend if longer runs are needed

DEMO_SCHEDULE = [
    GridEvent(
        name=f"{name}_d{day + 1}",
        event_type=ev_type,
        target="DC_NoMa",
        scheduled_at=float(day * TICKS_PER_DAY + offset),
        duration_steps=duration,
        params=params,
    )
    for day in range(_N_DAYS)
    for offset, ev_type, name, duration, params in _DAILY_PATTERN
]


# ── CSV logging ──────────────────────────────────────────────────────────────

LOG_PATH = Path(__file__).parent.parent / "data" / "live_log.csv"

_LOG_COLUMNS = [
    # Identity
    "timestamp_utc", "tick", "sim_time", "day", "day_tick",
    # Load profile
    "load_multiplier", "total_load_mw", "total_gen_mw", "total_sgen_mw",
    # DC_NoMa flexible load
    "dc_noma_mw",
    # Grid health
    "reserve_mw", "max_line_loading_pct", "min_voltage_pu",
    "n_violations", "converged",
    # Brain 1
    "brain1_risk", "action_needed",
    # Brain 2 (bottleneck warning or event response)
    "brain2_triggered", "brain2_action", "brain2_target", "brain2_confidence",
    # Event state
    "event_fired", "event_name", "event_type",
    "operator_choice",          # 1-4 / Enter / timeout / "" (no event)
    "active_events",
]


def _init_log() -> csv.DictWriter:
    """Reset and create the CSV log, returning a writer at the start of a fresh file."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = LOG_PATH.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=_LOG_COLUMNS)
    writer.writeheader()
    fh.flush()
    return writer, fh


def _log_tick(writer, tick: int, sim_time: str, multiplier: float,
              state: dict, pf: dict, risk: dict,
              b2: dict | None, b2_triggered: bool,
              fired_events: list, operator_choice: str,
              active_events: list, env) -> None:
    """Append one row to the live CSV log."""
    fired = fired_events[0] if fired_events else None
    dc_mw = float(env.grid.net.load.loc[
        env.grid.net.load["name"] == "DC_NoMa", "p_mw"
    ].values[0]) if "DC_NoMa" in env.grid.net.load["name"].values else 0.0

    row = {
        "timestamp_utc":      datetime.now(timezone.utc).isoformat(),
        "tick":               tick,
        "sim_time":           sim_time,
        "day":                tick // TICKS_PER_DAY + 1,
        "day_tick":           tick % TICKS_PER_DAY,
        "load_multiplier":    multiplier,
        "total_load_mw":      round(state.get("total_load_mw", 0), 2),
        "total_gen_mw":       round(state.get("total_gen_mw", 0), 2),
        "total_sgen_mw":      round(state.get("total_sgen_mw", 0), 2),
        "dc_noma_mw":         round(dc_mw, 2),
        "reserve_mw":         round(state.get("reserve_margin_mw", 0), 2),
        "max_line_loading_pct": round(state.get("max_line_loading_pct", 0), 2),
        "min_voltage_pu":     round(state.get("min_bus_voltage_pu", 0), 4),
        "n_violations":       pf.get("n_violations", 0),
        "converged":          int(state.get("converged", False)),
        "brain1_risk":        round(risk.get("overall_risk", 0), 4),
        "action_needed":      int(risk.get("action_needed", False)),
        "brain2_triggered":   int(b2_triggered),
        "brain2_action":      b2.get("action", "")      if b2 else "",
        "brain2_target":      b2.get("action_target", "") if b2 else "",
        "brain2_confidence":  b2.get("confidence", "")  if b2 else "",
        "event_fired":        int(bool(fired)),
        "event_name":         fired.name                if fired else "",
        "event_type":         fired.event_type.value    if fired else "",
        "operator_choice":    operator_choice,
        "active_events":      "|".join(e.name for e in active_events),
    }
    writer.writerow(row)


# ── Operator console helpers ──────────────────────────────────────────────────

def _timed_input(prompt: str, timeout: float) -> str:
    """Read a line from stdin with a wall-clock timeout. Returns '' on timeout."""
    result = [None]

    def _read():
        try:
            result[0] = input(prompt)
        except EOFError:
            result[0] = ""

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout=timeout)
    return result[0] if result[0] is not None else ""


def _operator_console(ev: GridEvent, b2: dict, scheduler: EventScheduler) -> str:
    """
    Brain 2 presents a numbered action menu to the operator.
    Any valid key (1-4 or Enter) triggers resolve_event().
    Timeout → event persists. Returns the operator's raw response string.
    """
    bar   = "=" * 70
    tmin  = OPERATOR_TIMEOUT_SECONDS // 60

    print(f"\n{bar}")
    print(f"  [BRAIN 2 — OPERATOR ACTION REQUIRED]")
    print(f"  {'─' * 66}")
    print(f"  Event      : {ev.name}  ({ev.event_type.value})")
    print(f"  Target     : {ev.target}")
    print(f"  {'─' * 66}")
    print(f"  THREAT     : {b2['threat_summary']}")
    print(f"  REASONING  : {b2['reasoning']}")
    print(f"  CONFIDENCE : {b2['confidence']}")
    print(f"  {'─' * 66}")
    print(f"  RECOMMENDATION  →  {b2['action']}  on  {b2['action_target']}")
    print(f"  {'─' * 66}")
    print(f"  Choose an action ({tmin}-min timeout — no response = event persists):\n")
    print(f"    [1]  {b2['action'].upper()} on {b2['action_target']}  ← Brain 2 recommendation")
    print(f"    [2]  DEFER_WORKLOAD   — postpone deferrable DC_NoMa jobs")
    print(f"    [3]  CURTAIL_LOAD     — reduce DC_NoMa below baseline now")
    print(f"    [4]  RESTORE_BASELINE — return DC_NoMa to normal draw")
    print(f"  [Enter]  Acknowledge and resolve\n")
    print(f"{bar}")

    response = _timed_input("  Operator selection: ", timeout=OPERATOR_TIMEOUT_SECONDS)

    if response is None or response.strip() == "" and response != "":
        # timeout
        print(f"\n  [BRAIN 2] No operator response in {tmin} min — event persists.\n")
        return ""

    choice = response.strip()

    if choice in ("1", "2", "3", "4", ""):
        labels = {
            "1": b2["action"],
            "2": "defer_workload",
            "3": "curtail_load",
            "4": "restore_baseline",
            "":  "acknowledge",
        }
        print(f"\n  [BRAIN 2] Operator selected: {labels[choice].upper()}")
        print(f"  [BRAIN 2] Executing resolve_event() on '{ev.name}'...")
        resolved = scheduler.force_resolve(ev.name)
        if resolved:
            print(f"  [BRAIN 2] Event resolved. Grid returning to baseline.\n")
        else:
            print(f"  [BRAIN 2] Warning: event already expired before resolution.\n")
    else:
        print(f"\n  [BRAIN 2] Unrecognized input — event persists.\n")

    return choice




# ── Event / tick display ──────────────────────────────────────────────────────

def _print_event_banner(ev: GridEvent, status: str) -> None:
    bar = "!" * 70
    print(f"\n{bar}")
    print(f"  [EVENT {status}]  {ev.name}")
    print(f"  Type    : {ev.event_type.value}")
    print(f"  Target  : {ev.target}")
    if ev.params:
        pairs = "  |  ".join(f"{k}={v}" for k, v in ev.params.items())
        print(f"  Params  : {pairs}")
    if status == "FIRED":
        print(f"  Duration: {ev.duration_steps} ticks ({ev.duration_steps * MINS_PER_TICK} min)")
    print(f"{bar}\n")


def _tick_to_time(tick: int) -> str:
    day_tick = tick % TICKS_PER_DAY
    hour     = day_tick // TICKS_PER_HOUR
    minute   = (day_tick % TICKS_PER_HOUR) * MINS_PER_TICK
    return f"{hour:02d}:{minute:02d}"


def apply_load_profile(env: SimulationEnvironment, day_tick: int) -> None:
    # Scale all static loads to the 10-minute slot's demand multiplier.
    multiplier = LOAD_PROFILE[day_tick] * BASE_LOAD_SCALE
    net = env.grid.net
    for idx in net.load.index:
        name = net.load.loc[idx, "name"]
        # Don't scale the flexible load — events control that directly
        if name == "DC_NoMa":
            continue
        if name in env.grid.load_specs:
            baseline = env.grid.load_specs[name].p_mw
        else:
            baseline = net.load.loc[idx, "p_mw"]
        net.load.loc[idx, "p_mw"] = baseline * multiplier


def print_tick(tick: int, time_str: str, state: dict, agent_result: dict,
               multiplier: float, active_events: list = None) -> None:
    sep = "─" * 70

    total_load = state["total_load_mw"]
    total_gen  = state["total_gen_mw"] + state["total_sgen_mw"]
    reserve    = state.get("reserve_margin_mw", 0.0)
    max_line   = state["max_line_loading_pct"]
    v_min      = state["min_bus_voltage_pu"]

    actions = []
    if agent_result:
        actions = [a["action"] for a in agent_result.get("actions", [])]
    action_str = ", ".join(actions) if actions else "nominal"

    violations = []
    if agent_result:
        v = agent_result.get("violations", {})
        if v.get("line_loading"):   violations.append("LINE OVERLOAD")
        if v.get("voltage_min"):    violations.append("LOW VOLTAGE")
        if v.get("reserve_margin"): violations.append("LOW RESERVE")
    alert = f"  ⚠  {', '.join(violations)}" if violations else ""

    day = tick // TICKS_PER_DAY + 1
    print(sep)
    print(f"  Day {day}  |  {time_str}  |  Tick {tick:>5}  |  Load ×{multiplier:.4f}")
    print(f"  Load     : {total_load:>7.1f} MW    Generation : {total_gen:>7.1f} MW")
    print(f"  Reserve  : {reserve:>7.1f} MW    Line max   : {max_line:>6.1f}%")
    print(f"  V min    : {v_min:.4f} p.u.   Agent      : {action_str}{alert}")
    if active_events:
        ev_str = "  |  ".join(
            f"{e.event_type.value}@{e.target}" for e in active_events
        )
        print(f"  Active   : {ev_str}")


# ── Main live loop ────────────────────────────────────────────────────────────

def run_live(tick_seconds: float = 1.0, max_ticks: int = None) -> None:
    print("\n" + "=" * 70)
    print("  PEPCO DC GRID — LIVE SIMULATION  (10 min/tick)")
    print(f"  Logging to: {LOG_PATH}")
    print("  Press Ctrl+C to stop")
    print("=" * 70 + "\n")

    env = SimulationEnvironment()
    env.build_grid()
    env.initialize()

    scheduler = EventScheduler(env.grid)
    for ev in DEMO_SCHEDULE:
        scheduler.schedule(ev)
    print(f"[LIVE] {len(DEMO_SCHEDULE)} demo events scheduled ({_N_DAYS} days × 4 events).\n")

    log_writer, log_fh = _init_log()

    tick = 0
    try:
        while True:
            if max_ticks is not None and tick >= max_ticks:
                print(f"\n[LIVE] Reached {max_ticks} ticks — stopping.")
                break

            day_tick   = tick % TICKS_PER_DAY
            time_str   = _tick_to_time(tick)
            multiplier = LOAD_PROFILE[day_tick]

            apply_load_profile(env, day_tick)

            event_results = scheduler.tick(float(tick))
            for ev in event_results["expired"]:
                _print_event_banner(ev, "CLEARED")

            result = env.step()
            if not result.get("converged"):
                print(f"[LIVE] Power flow failed at tick {tick} — stopping.")
                break

            pf    = result.get("pf_report") or {}
            agent = result.get("agent_result") or {}
            risk  = brain1(pf, result)

            b2_last       = None
            b2_triggered  = False
            operator_choice = ""

            # For each newly fired event: print banner, run Brain 2, pause for operator
            for ev in event_results["applied"]:
                _print_event_banner(ev, "FIRED")
                b2_last      = brain2(risk, agent, tick)
                b2_triggered = True
                operator_choice = _operator_console(ev, b2_last, scheduler)

            # Bottleneck warning — no immediate action required
            if not event_results["applied"] and risk["action_needed"]:
                b2_last      = brain2(risk, agent, tick)
                b2_triggered = True
                print(f"\n  [BOTTLENECK WARNING]  {b2_last['threat_summary']}")
                print(f"  Recommended action: {b2_last['action']} → {b2_last['action_target']}  |  confidence: {b2_last['confidence']}\n")

            print_tick(
                tick=tick,
                time_str=time_str,
                state=result["grid_state"],
                agent_result=agent,
                multiplier=multiplier,
                active_events=scheduler.active_events(),
            )

            _log_tick(
                writer=log_writer,
                tick=tick,
                sim_time=time_str,
                multiplier=multiplier,
                state=result["grid_state"],
                pf=pf,
                risk=risk,
                b2=b2_last,
                b2_triggered=b2_triggered,
                fired_events=event_results["applied"],
                operator_choice=operator_choice,
                active_events=scheduler.active_events(),
                env=env,
            )
            log_fh.flush()

            tick += 1
            time.sleep(tick_seconds)

    except KeyboardInterrupt:
        elapsed_min = tick * MINS_PER_TICK
        print(f"\n\n[LIVE] Stopped by operator after {tick} ticks "
              f"({elapsed_min // 60}h {elapsed_min % 60}m simulated time).")
    finally:
        log_fh.close()


# ── Dashboard helper ──────────────────────────────────────────────────────────
# Run N ticks of the live simulation and return records for the dashboard.
# No operator prompts — events auto-expire at their natural duration.
# Default is one full simulated day (144 ticks × 10 min = 24 hours).
def run(ticks: int = 144) -> list:
    env = SimulationEnvironment()
    env.build_grid()
    env.initialize()

    scheduler = EventScheduler(env.grid)
    for ev in DEMO_SCHEDULE:
        scheduler.schedule(ev)

    records = []
    for tick in range(ticks):
        day_tick   = tick % TICKS_PER_DAY
        time_str   = _tick_to_time(tick)
        multiplier = LOAD_PROFILE[day_tick]
        apply_load_profile(env, day_tick)
        scheduler.tick(float(tick))

        result = env.step()
        pf     = result.get("pf_report") or {}
        state  = result.get("grid_state") or {}
        risk   = brain1(pf, result)

        b2_action = b2_target = b2_summary = b2_confidence = ""
        if risk["action_needed"]:
            b2            = brain2(risk, {}, tick)
            b2_action     = b2.get("action", "")
            b2_target     = b2.get("action_target", "")
            b2_summary    = b2.get("threat_summary", "")
            b2_confidence = b2.get("confidence", "")

        records.append({
            "tick":              tick,
            "hour":              time_str,
            "load_multiplier":   multiplier,
            "total_load_mw":     round(state.get("total_load_mw", 0), 1),
            "reserve_mw":        round(state.get("reserve_margin_mw", 0), 1),
            "line_loading_pct":  round(state.get("max_line_loading_pct", 0), 1),
            "min_voltage_pu":    round(state.get("min_bus_voltage_pu", 0), 4),
            "overall_risk":      risk["overall_risk"],
            "action_needed":     risk["action_needed"],
            "brain2_action":     b2_action,
            "brain2_target":     b2_target,
            "brain2_summary":    b2_summary,
            "brain2_confidence": b2_confidence,
            "n_violations":      pf.get("n_violations", 0),
            "converged":         result.get("converged", False),
            "active_events":     ", ".join(e.name for e in scheduler.active_events()),
        })

    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--speed", type=float, default=1.0,
        help="Seconds per tick (default 1.0). Lower = faster.",
    )
    parser.add_argument(
        "--ticks", type=int, default=None,
        help="Stop after N ticks (default: run indefinitely).",
    )
    args = parser.parse_args()
    run_live(tick_seconds=args.speed, max_ticks=args.ticks)
