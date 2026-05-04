# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Grid-Aware-Agent monitors real electricity load in PJM's Dominion (DOM) zone — Northern Virginia, the largest concentration of US AI data centers — detects demand spikes above a rolling baseline, and replays those spikes through a pandapower grid simulation where a rule-based agent recommends load-balancing actions (curtail or defer flexible data center loads) in response to constraint violations.

## Commands

```bash
# All scripts require these packages — use uv (system pip is broken on Python 3.13/3.14)
DEPS="--with requests --with python-dotenv --with pandas --with pandapower --with streamlit --with anthropic"

# 1. Test API connection
/opt/homebrew/bin/uv run $DEPS python3 src/fetch_load.py

# 2. Pull 12 months of DOM load, compute baseline, write data/spikes.csv
/opt/homebrew/bin/uv run $DEPS python3 src/baseline.py

# 3. Replay spikes through the grid simulator (CLI output)
/opt/homebrew/bin/uv run $DEPS python3 src/run_simulation.py --steps 20 --top-spikes

# 4. Launch the Streamlit dashboard at localhost:8501
/opt/homebrew/bin/uv run $DEPS streamlit run src/dashboard.py --server.headless true
```

API key lives in `.env` (gitignored): `GRIDSTATUS_API_KEY=your_key_here`

## Workflow

```
GridStatus API  (pjm_load_metered_hourly, DOM zone)
      │
      ▼
src/fetch_load.py     — pulls hourly MW for the past N days, paginates automatically
      │
      ▼
src/baseline.py       — rolling same-hour median over 8 weeks; flags hours where
      │                  actual/baseline > 1.08 as spike events
      ▼
data/spikes.csv       — output: [interval_start_utc, mw, baseline_mw, ratio]
      │
      ▼
src/run_simulation.py — loads spikes, scales delta MW to simulator range,
      │                  injects each spike as a LOAD_SPIKE event, runs power
      │                  flow + agent per step, returns structured results
      │
      ├── simulator/simulation.py   (SimulationEnvironment — orchestrator)
      │       ├── simulator/network.py     (GridNetwork + FlexibleLoad)
      │       ├── simulator/power_flow.py  (PowerFlowEngine — AC power flow)
      │       ├── simulator/events.py      (EventScheduler — inject/revert events)
      │       └── simulator/agent.py       (GridOptimizationAgent — rule-based)
      │
      ▼
src/dashboard.py      — Streamlit UI; Tab 1 = real spike data charts,
                         Tab 2 = simulation replay with agent action charts
```

## Architecture

### Data pipeline (`src/`)

| File | Role |
|------|------|
| `fetch_load.py` | Calls GridStatus `/v1/datasets/pjm_load_metered_hourly/query`. Filters to `load_area=DOM` (zone aggregate). Paginates with `page`/`hasNextPage`. Correct params are `start_time`/`end_time` (not `start`/`end`). |
| `baseline.py` | Groups by hour-of-day, applies `.rolling(56).median().shift(1)` per group (56 = 8 weeks × 7 same-hour observations). `shift(1)` prevents data leakage. |
| `run_simulation.py` | Bridge layer. Scales real DOM deltas (≈13,000 MW baseline) down to simulator scale (100 MW baseline) using `SCALE = 100/13000`. Uses `EventScheduler` + `SimulationEnvironment`. Returns list of dicts for dashboard. |
| `dashboard.py` | Two-tab Streamlit app. Uses `st.session_state` to preserve simulation results across re-renders. |

### Simulator layer (`simulator/`)

Built by teammate Francisco Vu. All modules require the project root on `sys.path`.

| File | Role |
|------|------|
| `network.py` | `GridNetwork` wraps pandapower. `FlexibleLoad` represents a data center — supports `curtail_load(pct)`, `defer_load(mw)`, `restore_baseline()`. Uses `pp.create_line_from_parameters` (not `pp.create_line` which requires a named std_type). |
| `power_flow.py` | `PowerFlowEngine` wraps `pp.runpp`. Checks line loading (%), voltage (p.u.), and reserve margin (MW). Reports per-component violations. Reserve formula: `gen + sgen - load`. |
| `events.py` | `EventScheduler` applies and reverts `GridEvent` objects each tick. Snapshot/restore pattern ensures grid state is cleanly reverted after event duration expires. |
| `simulation.py` | `SimulationEnvironment` wires grid → power flow → agent. Default 3-bus topology: Bus_Gen (slack, 500 MW coal) → Bus_Central → Bus_Load (200 MW city + 100 MW DC_Tyson + 50 MW solar). |
| `agent.py` | Rule-based: curtail 20% on line overload or low voltage; defer 15% on low reserve; restore otherwise. Runs after every power flow step. |

### Grid topology

```
Bus_Gen (345 kV, slack)
    │  Line_Gen_Central (50 km, 500 MVA)
Bus_Central (345 kV)
    │  Line_Central_Load (30 km, 250 MVA)
Bus_Load (110 kV)
    ├── Load_City         200 MW static
    ├── DC_Tyson          100 MW flexible (data center)
    └── Solar_Farm         50 MW static generator
```

### Scaling

Real DOM zone baseline ≈ 13,000 MW. Simulator DC_Tyson baseline = 100 MW.  
Spike deltas are multiplied by `100 / 13000 ≈ 0.0077` so relative stress (ratio) is preserved.

## Known environment issues

- Python 3.13 and 3.14 from Homebrew have a broken `libexpat` linkage — `pip` crashes. Use `uv` at `/opt/homebrew/bin/uv` instead.
- `simulator/power-flow.py` was renamed to `power_flow.py` (hyphens break Python imports).
- pandapower's `res_line` does not include `s_from_mva` or `sn_mva` when lines are created with `create_line_from_parameters` — apparent power is computed from `p_from_mw` and `q_from_mvar`.

## Repository

Remote: `https://github.com/franware1/Grid-Aware-Agent.git`  
Branches: `main` (simulator code by Francisco), `Aayam` (data pipeline + integration)
