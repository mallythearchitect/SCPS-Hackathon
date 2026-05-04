# Kashimo

Kashimo is an AI-powered energy management system that simulates the Washington DC power grid (modeled on Pepco's actual topology) and responds to demand disruptions caused by AI data centers. A rule-based risk scorer (Brain 1) monitors the grid every tick; when risk crosses a threshold, a Claude-powered reasoning agent (Brain 2) recommends an action to a human operator.

---

## Quick Start

Run each command in a separate terminal from the project root.

**1. Start the simulation**
```bash
python src/run_live.py
```

**2. Open the dashboard**
```bash
streamlit run interface/frontend/app.py
```

The dashboard connects automatically once the simulation starts writing data. The log resets every time `run_live.py` is restarted.

---

## Simulation flags

```bash
python src/run_live.py                  # default — 1 tick per second
python src/run_live.py --speed 0.25     # fast mode — 4 ticks per second
python src/run_live.py --speed 5        # slow mode — 1 tick per 5 seconds
python src/run_live.py --ticks 288      # stop after exactly 2 simulated days
```

One tick = 30 simulated minutes. One full day = 48 ticks.

---

## How it works

### Grid model

The simulation is built on the actual Pepco Washington DC network:

- **7 transmission substations** connected in a 345 kV ring (Benning Road, East Capitol, Greenway, Hains Point, Buzzard Point, Georgetown, Nevada Avenue)
- **51 distribution substations** across all DC quadrants
- **10 transmission lines** linking the ring
- **3 generators** — Benning Road (850 MW, slack bus), Georgetown (200 MW), Buzzard Point (150 MW) — 2,050 MW total capacity
- **1 flexible load** — DC NoMa data center, 110 MW baseline, range 80–140 MW
- **6 DER units** — rooftop solar across NE/SE/NW DC, 70 MW total

AC power flow runs via [pandapower](https://pandapower.org/) at every tick.

### Daily event schedule

Four AI data center disruptions fire automatically each simulated day:

| Clock time | Event | Description |
|---|---|---|
| 06:00 | AI Training Spike | Large training job launches — demand jumps 25–60 MW |
| 14:00 | AI Training Dropout | Job crashes mid-run — demand drops 75% instantly |
| 18:00 | Cooling Cascade | Compute surge followed by a 30-min thermal lag from chillers |
| 21:00 | Load Oscillation | Power-electronics hunting — demand swings rhythmically |

### Brain 1 — Risk scorer

Evaluates three signals after every power flow solve and produces a 0–1 risk score:

| Signal | Warn | Critical |
|---|---|---|
| Line loading | ≥ 75% | ≥ 90% |
| Reserve margin | ≤ 400 MW | ≤ 300 MW |
| Bus voltage | ≤ 0.97 pu | ≤ 0.95 pu |

Overall risk = max of all component scores. If overall ≥ 0.65, Brain 2 is triggered.

### Brain 2 — AI reasoning agent

Powered by Claude Sonnet. Reads Brain 1's scores, external context (EIA demand, weather, LMP pricing), and the current event. Reasons about what action to take and presents a numbered menu to the operator in the terminal:

```
[1]  <Brain 2 recommendation>
[2]  DEFER_WORKLOAD   — postpone deferrable DC_NoMa jobs
[3]  CURTAIL_LOAD     — reduce DC_NoMa below baseline now
[4]  RESTORE_BASELINE — return DC_NoMa to normal draw
[Enter]  Acknowledge and resolve
```

The operator has **5 minutes** to respond. No response = event persists until its natural duration expires.

---

## Dashboard

The Streamlit dashboard (`interface/frontend/app.py`) reads from `data/live_log.csv` in real time and updates automatically whenever a new tick is written.

| Tab | Content |
|---|---|
| Live Monitor | Demand, reserve, line loading, and risk trend charts |
| Power Lines | Per-line loading bars, generator status, DER output |
| AI Agent | Brain 1 component scores, Brain 2 recommendation and reasoning |
| Event Log | Full record of fired events and operator responses |

When Brain 2 is active, an action panel appears at the top of the dashboard directing the operator to respond in the simulation terminal.

---

## Project structure

```
Grid-Aware-Agent/
├── src/
│   └── run_live.py              # Live simulation entry point
├── simulator/
│   ├── build_simulation.py      # Wires grid + power flow + agent from config
│   ├── network.py               # pandapower grid wrapper + FlexibleLoad
│   ├── power_flow.py            # AC power flow engine + violation checks
│   ├── events.py                # Event scheduler — inject / revert grid events
│   ├── brain1.py                # Rule-based risk scorer
│   ├── brain2.py                # Claude reasoning agent
│   └── config/grid_config.json  # Full Pepco DC topology definition
├── interface/
│   ├── frontend/
│   │   └── app.py               # Streamlit dashboard
│   └── backend/
│       ├── grid_connection.py   # Reads live_log.csv, detects simulation state
│       └── constants.py         # Thresholds, labels, event definitions
├── data/
│   └── live_log.csv             # Written by run_live.py, read by dashboard
└── .env                         # API keys (gitignored)
```

---

## Setup

### Dependencies

```bash
pip install pandapower streamlit pandas anthropic requests python-dotenv
```

### Environment variables

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=your_key_here
GRIDSTATUS_API_KEY=your_key_here
```

`ANTHROPIC_API_KEY` is required for Brain 2. `GRIDSTATUS_API_KEY` is used by Brain 2 for real-time market context (falls back to stubs if unavailable).

---

## Authors

Francisco Vu, Aayam Mainali, Wenley Jean-Pierre, Malachi Collins
