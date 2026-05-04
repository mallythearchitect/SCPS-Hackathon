"""
simulator/brain1.py
Brain 1 — Risk Scorer

Reads pf_report from env.step()['pf_report'] and produces
a structured risk assessment for Brain 2 to reason over.

No LLM here. Pure numerical scoring based on grid physics.
Slot for XGBoost/LSTM when Brain 1 person is ready —
swap score() internals, keep the output shape identical.
"""


# ── Thresholds (tuned to their Pepco DC grid) ─────────────────────────────────

LINE_WARN     = 75.0    # % — elevated
LINE_HIGH     = 85.0    # % — high
LINE_CRITICAL = 90.0    # % — agent must act

VOLT_LOW_CRIT  = 0.95   # pu — NERC lower limit
VOLT_LOW_WARN  = 0.97   # pu — approaching limit
VOLT_HIGH_CRIT = 1.05   # pu — NERC upper limit
VOLT_HIGH_WARN = 1.03   # pu — approaching limit

RESERVE_CRITICAL = 300.0  # MW — matches grid.constraints in network.py
RESERVE_WARN     = 400.0  # MW


# ── Scoring functions ─────────────────────────────────────────────────────────

def _line_risk(pct: float) -> float:
    if pct >= LINE_CRITICAL: return 0.90
    if pct >= LINE_HIGH:     return 0.65
    if pct >= LINE_WARN:     return 0.35
    return 0.10


def _voltage_risk(vm_pu: float) -> float:
    if vm_pu <= VOLT_LOW_CRIT:  return 0.90
    if vm_pu <= VOLT_LOW_WARN:  return 0.40
    if vm_pu >= VOLT_HIGH_CRIT: return 0.90
    if vm_pu >= VOLT_HIGH_WARN: return 0.30
    return 0.05


def _reserve_risk(mw: float) -> float:
    if mw <= RESERVE_CRITICAL: return 0.90
    if mw <= RESERVE_WARN:     return 0.50
    return 0.10


# ── Main entry point ──────────────────────────────────────────────────────────

def score(pf_report: dict, step_result: dict) -> dict:
    """
    Produce a risk assessment from one env.step() output.

    Args:
        pf_report:   step_result['pf_report'] from SimulationEnvironment.step()
        step_result: full step dict (for grid_state summary values)

    Returns:
        {
          "overall_risk":  float,        # 0-1, max across all components
          "action_needed": bool,         # True if any score >= 0.65
          "top_threat":    str,          # name of worst component
          "lines": {
              name: {"score": float, "loading_pct": float, "critical": bool}
          },
          "system": {
              "reserve_score":   float,
              "reserve_mw":      float,
              "voltage_min_pu":  float,
              "voltage_max_pu":  float,
              "voltage_score":   float,
          },
          "violations": list,            # pass-through from pf_report
          "dc_noma": {
              "current_mw":   float,
              "baseline_mw":  float,
              "deferrable_mw": float,
          }
        }
    """
    # Convergence failure — maximum risk
    if not pf_report or pf_report.get("status") == "failed":
        return {
            "overall_risk":  1.0,
            "action_needed": True,
            "top_threat":    "convergence_failure",
            "lines":         {},
            "system":        {},
            "violations":    [],
            "dc_noma":       {},
        }

    summary    = pf_report.get("summary",    {})
    lines_data = pf_report.get("lines",      {})
    loads_data = pf_report.get("loads",      {})
    violations = pf_report.get("violations", [])

    # ── Score each line ───────────────────────────────────────────────────────
    line_scores = {}
    for name, state in lines_data.items():
        loading = state.get("loading_percent", 0.0)
        s = _line_risk(loading)
        line_scores[name] = {
            "score":       round(s, 3),
            "loading_pct": round(loading, 1),
            "critical":    loading >= LINE_CRITICAL,
        }

    # ── Score system-level signals ────────────────────────────────────────────
    reserve_mw = summary.get("reserve_margin_mw", 999.0)
    min_v      = summary.get("min_bus_voltage_pu", 1.0)
    max_v      = summary.get("max_bus_voltage_pu", 1.0)

    v_score = max(_voltage_risk(min_v), _voltage_risk(max_v))
    r_score = _reserve_risk(reserve_mw)

    system = {
        "reserve_score":  round(r_score, 3),
        "reserve_mw":     round(reserve_mw, 1),
        "voltage_min_pu": round(min_v, 4),
        "voltage_max_pu": round(max_v, 4),
        "voltage_score":  round(v_score, 3),
    }

    # ── DC_NoMa flexible load state ───────────────────────────────────────────
    # Their FlexibleLoad sits in net.load — grab it from loads_data
    noma_load = loads_data.get("DC_NoMa", {})
    dc_noma = {
        "current_mw":    round(noma_load.get("p_mw", 0.0), 1),
        "baseline_mw":   110.0,   # from FlexibleLoadSpec in simulation.py
        "deferrable_mw": round(110.0 * 0.25, 1),  # 25% deferrable per spec
    }

    # ── Overall risk and top threat ───────────────────────────────────────────
    all_scores = (
        [v["score"] for v in line_scores.values()]
        + [r_score, v_score]
    )
    overall = round(max(all_scores), 3)

    # Find the single worst component to surface to Brain 2
    worst_line = max(line_scores.items(), key=lambda x: x[1]["score"], default=(None, {"score": 0}))
    candidates = {
        worst_line[0]: worst_line[1]["score"] if worst_line[0] else 0,
        "reserve":     r_score,
        "voltage":     v_score,
    }
    top_threat = max(candidates, key=candidates.get)

    return {
        "overall_risk":  overall,
        "action_needed": overall >= 0.65,
        "top_threat":    top_threat,
        "lines":         line_scores,
        "system":        system,
        "violations":    violations,
        "dc_noma":       dc_noma,
    }