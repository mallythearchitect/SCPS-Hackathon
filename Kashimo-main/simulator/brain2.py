"""
simulator/brain2.py
Brain 2 — LLM Reasoning Agent

Takes Brain 1's risk assessment + external context and returns
a structured operator action with plain-English explanation.

This runs alongside their GridOptimizationAgent — it doesn't
replace it. Their agent acts on the grid directly. Ours reasons
about what happened and why, and recommends the next move.
"""

import os
import json
import anthropic
from datetime import datetime
from pathlib import Path

# Load .env from project root without requiring python-dotenv
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

VALID_ACTIONS = {
    "defer_workload",       # tell DC_NoMa to postpone deferrable jobs
    "curtail_load",         # reduce DC_NoMa below baseline now
    "restore_baseline",     # grid recovered, return DC to normal
    "alert_operator",       # flag to human — too complex for auto-action
    "no_action",            # risk below threshold, all good
}


# ── External context stubs ────────────────────────────────────────────────────
# Swap these for real API calls when keys are available.
# Kept here so Brain 2 has one import — no dependency on context_builder.py.

def _eia_context(time_step: int) -> dict:
    """EIA Open Data — PJM DOM zone demand. Stub: realistic afternoon curve."""
    base = 13_000
    mw = base + time_step * 85
    return {
        "region": "PJM_DOM",
        "current_mw": mw,
        "forecast_1h_mw": mw + 420,
        "trend": "rising" if time_step < 45 else "falling",
    }


def _weather_context() -> dict:
    """NREL stub — Northern Virginia summer afternoon."""
    return {
        "temp_c": 31.2,
        "cooling_pressure": "high",
        "solar_wm2": 820,
    }


def _gridstatus_context(time_step: int) -> dict:
    """gridstatus.io stub — LMP and congestion."""
    lmp = 52.0 + time_step * 0.9
    return {
        "lmp_per_mwh": round(lmp, 2),
        "congested": lmp > 70,
    }


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(risk: dict, context: dict, their_action: dict, time_step: int) -> str:
    """
    Assemble the full prompt from Brain 1 output + external context
    + what their rule-based agent already did this step.
    """
    # Worst lines for the prompt — top 3 by loading
    top_lines = sorted(
        risk["lines"].items(),
        key=lambda x: x[1]["loading_pct"],
        reverse=True
    )[:3]

    lines_str = "\n".join(
        f"  {name}: {v['loading_pct']}%"
        + (" [CRITICAL]" if v["critical"] else "")
        for name, v in top_lines
    ) or "  No line data"

    violations_str = "\n".join(
        f"  [{v['severity']}] {v['type']}: {v['component']} "
        f"= {v['current']:.2f} (limit {v['limit']:.2f})"
        for v in risk["violations"]
    ) or "  None"

    their_actions_str = "\n".join(
        f"  {a['load']}: {a['action']} ({a['reason']})"
        for a in their_action.get("actions", [])
    ) or "  None taken"

    dc = risk.get("dc_noma", {})
    sys = risk.get("system", {})
    eia = context["eia"]
    wx  = context["weather"]
    mkt = context["market"]

    return f"""You are a grid operations AI assistant for the Pepco Washington DC distribution grid.
Time step: {time_step} | {datetime.now().strftime('%H:%M:%S')}

## Risk assessment (Brain 1 output)
Overall risk score: {risk['overall_risk']} (0=safe, 1=critical)
Top threat: {risk['top_threat']}
Action needed: {risk['action_needed']}

## Grid state
Top loaded lines:
{lines_str}

System:
  Reserve margin: {sys.get('reserve_mw', 'N/A')} MW (score: {sys.get('reserve_score', 'N/A')})
  Voltage range: {sys.get('voltage_min_pu', 'N/A')}–{sys.get('voltage_max_pu', 'N/A')} pu (score: {sys.get('voltage_score', 'N/A')})

Constraint violations:
{violations_str}

## DC_NoMa flexible load (the lever we can pull)
  Current draw:   {dc.get('current_mw', 'N/A')} MW
  Baseline:       {dc.get('baseline_mw', 'N/A')} MW
  Max deferrable: {dc.get('deferrable_mw', 'N/A')} MW (25% of baseline)

## What the rule-based agent already did this step
{their_actions_str}

## External context
Regional demand: {eia['current_mw']:,} MW (PJM DOM), trend: {eia['trend']}
Forecast +1h: {eia['forecast_1h_mw']:,} MW
LMP: ${mkt['lmp_per_mwh']}/MWh {'[CONGESTED]' if mkt['congested'] else ''}
Weather: {wx['temp_c']}°C, cooling pressure: {wx['cooling_pressure']}

## Your task
The rule-based agent reacts to violations that already exist.
Your job is to reason one step ahead — catch what it misses and explain why.

1. Identify the real threat in 1-2 sentences an operator can act on.
2. Choose exactly ONE action: {" | ".join(sorted(VALID_ACTIONS))}
3. Name the target: DC_NoMa, a specific line name, or "operator".
4. Confidence: low | medium | high
5. Reasoning: 2-3 sentences. Mention what the rule-based agent did and whether it's sufficient.

Respond ONLY in valid JSON, no markdown:
{{
  "threat_summary": "...",
  "action": "...",
  "action_target": "...",
  "confidence": "...",
  "reasoning": "..."
}}"""


# ── Main entry point ──────────────────────────────────────────────────────────

def run(risk: dict, their_action: dict, time_step: int) -> dict:
    """
    Run Brain 2 reasoning.

    Args:
        risk:        output of brain1.score()
        their_action: step_result['agent_result'] from SimulationEnvironment
        time_step:   current simulation step number

    Returns:
        Structured action dict with threat_summary, action, reasoning, etc.
    """
    context = {
        "eia":     _eia_context(time_step),
        "weather": _weather_context(),
        "market":  _gridstatus_context(time_step),
    }

    prompt = _build_prompt(risk, context, their_action, time_step)

    try:
        response = _client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        action = json.loads(raw)

        # Validate action field
        if action.get("action") not in VALID_ACTIONS:
            action["action"] = "alert_operator"
            action["action_target"] = "operator"
            action["confidence"] = "low"

        action["time_step"]    = time_step
        action["overall_risk"] = risk["overall_risk"]
        action["top_threat"]   = risk["top_threat"]
        return action

    except (json.JSONDecodeError, KeyError, IndexError, Exception) as e:
        return {
            "time_step":      time_step,
            "overall_risk":   risk.get("overall_risk", -1),
            "top_threat":     risk.get("top_threat", "unknown"),
            "threat_summary": "Brain 2 parse error — defaulting to operator alert.",
            "action":         "alert_operator",
            "action_target":  "operator",
            "confidence":     "low",
            "reasoning":      str(e),
        }