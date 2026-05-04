"""
frontend/app.py  —  GridAgent Live Operations Dashboard
Reads all data from backend.grid_connection (live_log.csv written by run_live.py).

Run:
    streamlit run interface/frontend/app.py
"""

import base64
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# ── Import path: make `backend` package visible ────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))  # interface/

from backend.grid_connection import get_connection_status, get_history, get_latest_state, OPERATOR_RESP_PATH
from backend.constants import ACTION_PLAIN, DC_STATE_PLAIN, TX_LINES, BASE_LINE_PCT
from backend.chat import chat as operator_chat

# ── Logo ───────────────────────────────────────────────────────────────────────
_LOGO_PATH = Path(__file__).parent / "assets" / "kashimo3.png"
_LOGO_B64  = base64.b64encode(_LOGO_PATH.read_bytes()).decode() if _LOGO_PATH.exists() else ""
_LOGO_IMG  = f'<img src="data:image/png;base64,{_LOGO_B64}" style="height:48px;border-radius:3px;margin-right:12px">' if _LOGO_B64 else ""

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="GridAgent // Pepco DC",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS (identical to dashboard.py) ───────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;500;600;700&display=swap');
html,body,[data-testid="stApp"]{background:#060a0f!important;color:#a8c8a0!important;font-family:'Rajdhani',sans-serif!important;}
#MainMenu,footer,header,[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"]{display:none!important;}
.main .block-container{padding:0!important;max-width:100%!important;}

.ems-header{background:#0a1a0e;border-bottom:1px solid #1a4a22;padding:10px 24px;display:flex;align-items:center;justify-content:space-between;}
.ems-logo{font-family:'Share Tech Mono';font-size:13px;color:#2ecc71;letter-spacing:.15em;text-transform:uppercase;}
.ems-logo span{color:#7fff7f;}
.ems-clock{font-family:'Share Tech Mono';font-size:24px;color:#c8f0c0;letter-spacing:.1em;}
.ems-sub{font-family:'Share Tech Mono';font-size:9px;color:#2a5a2a;letter-spacing:.1em;margin-top:2px;}

.kpi-strip{background:#080e10;border-bottom:1px solid #112211;padding:0 12px;display:flex;}
.kpi-block{flex:1;padding:10px 14px;border-right:1px solid #0d1e0d;}
.kpi-block:last-child{border-right:none;}
.kpi-label{font-family:'Share Tech Mono';font-size:9px;color:#3a6a3a;letter-spacing:.12em;text-transform:uppercase;margin-bottom:3px;}
.kpi-value{font-family:'Share Tech Mono';font-size:26px;line-height:1;letter-spacing:.04em;}
.kpi-unit{font-family:'Share Tech Mono';font-size:9px;color:#3a6a3a;letter-spacing:.1em;margin-top:2px;}
.kpi-ok{color:#2ecc71;}.kpi-warn{color:#f0a030;}.kpi-crit{color:#e74c3c;}.kpi-info{color:#4ac8f0;}.kpi-dim{color:#7fff7f;}

.alarm-strip{background:#04090c;border-bottom:2px solid #1a1a00;padding:5px 16px;display:flex;gap:8px;align-items:center;min-height:34px;overflow-x:auto;}
.alarm-item{font-family:'Share Tech Mono';font-size:10px;padding:3px 8px;border-radius:2px;white-space:nowrap;letter-spacing:.06em;}
.alarm-crit{background:rgba(231,76,60,.15);border:1px solid #e74c3c;color:#ff6b6b;animation:blink 1.2s infinite;}
.alarm-warn{background:rgba(240,160,48,.12);border:1px solid #f0a030;color:#ffc060;}
.alarm-ok{background:rgba(46,204,113,.08);border:1px solid #1a6a2a;color:#2ecc71;}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.45}}
.alarm-ts{font-family:'Share Tech Mono';font-size:9px;color:#2a5a2a;margin-left:auto;}

.scada-card{background:#080e10;border:1px solid #112211;border-top:2px solid #1a4a22;padding:12px 16px;}
.scada-card-title{font-family:'Share Tech Mono';font-size:9px;color:#2a5a2a;letter-spacing:.18em;text-transform:uppercase;margin-bottom:10px;border-bottom:1px solid #0a1e0a;padding-bottom:6px;}
.oneline-row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #0a1a0a;font-family:'Share Tech Mono';font-size:11px;}
.oneline-row:last-child{border-bottom:none;}
.oneline-name{color:#5a9a5a;font-size:10px;}
.oneline-val{color:#c8f0c0;}
.oneline-status{font-size:9px;padding:2px 6px;border-radius:1px;}
.st-ok{background:rgba(46,204,113,.1);color:#2ecc71;border:1px solid #1a5a1a;}
.st-warn{background:rgba(240,160,48,.1);color:#f0a030;border:1px solid #6a4a10;}
.st-crit{background:rgba(231,76,60,.1);color:#e74c3c;border:1px solid #6a1a1a;}

.risk-big{font-family:'Share Tech Mono';font-size:48px;line-height:1;letter-spacing:.04em;}
.risk-bar-wrap{height:6px;background:#0d1f0d;border-radius:3px;margin:8px 0;overflow:hidden;}
.risk-bar{height:100%;border-radius:3px;}

.b2-box{background:rgba(42,122,170,.08);border:1px solid #1a4a6a;border-left:3px solid #2a7aaa;padding:12px 14px;margin:6px 0;border-radius:2px;}
.plain-box{background:rgba(46,204,113,.04);border:1px solid #1a4a22;border-left:3px solid #2ecc71;padding:10px 14px;border-radius:2px;margin-top:8px;}
.plain-title{font-family:'Share Tech Mono';font-size:9px;color:#2ecc71;letter-spacing:.15em;text-transform:uppercase;margin-bottom:4px;}
.plain-text{font-family:'Rajdhani';font-size:13px;color:#a8d8a0;line-height:1.5;}

.evlog-row{display:flex;gap:8px;align-items:flex-start;padding:5px 0;border-bottom:1px solid #0a150a;}
.evlog-tick{font-family:'Share Tech Mono';font-size:9px;color:#2a4a2a;min-width:52px;}
.evlog-dot{width:5px;height:5px;border-radius:50%;margin-top:4px;flex-shrink:0;}
.evlog-msg{font-family:'Share Tech Mono';font-size:10px;color:#6ab06a;line-height:1.4;}
.log-scroll{height:180px;overflow-y:auto;}
.log-scroll::-webkit-scrollbar{width:3px;}
.log-scroll::-webkit-scrollbar-thumb{background:#1a3a1a;border-radius:2px;}

.stButton>button{
    background:#0a1e0a!important;border:1px solid #1a5a1a!important;
    color:#2ecc71!important;font-family:'Share Tech Mono'!important;
    font-size:11px!important;letter-spacing:.08em!important;
    border-radius:2px!important;padding:6px 18px!important;width:100%;
    transition:all .2s;
}
.stButton>button:hover{background:#0d2e0d!important;border-color:#2ecc71!important;}

[data-testid="stSelectbox"] label,[data-testid="stSlider"] label{font-family:'Share Tech Mono'!important;font-size:10px!important;color:#2a5a2a!important;letter-spacing:.1em!important;}
[data-testid="stSelectbox"]>div>div{background:#080e10!important;border:1px solid #1a4a1a!important;border-radius:2px!important;color:#a8c8a0!important;font-family:'Share Tech Mono'!important;font-size:11px!important;}
[data-testid="stTabs"] [role="tab"]{font-family:'Share Tech Mono'!important;font-size:10px!important;letter-spacing:.12em!important;color:#3a6a3a!important;background:transparent!important;border-bottom:2px solid transparent!important;}
[data-testid="stTabs"] [role="tab"][aria-selected="true"]{color:#2ecc71!important;border-bottom:2px solid #2ecc71!important;}
[data-testid="stTabs"] [role="tablist"]{background:#060a0f!important;border-bottom:1px solid #112211!important;}
[data-testid="stTab"]{background:#060a0f!important;padding:0!important;}
div[data-testid="column"]{padding:4px 6px!important;}
[data-testid="stVerticalBlock"]{gap:.4rem!important;}
</style>
""", unsafe_allow_html=True)

# ── Constants ──────────────────────────────────────────────────────────────────
DC_BASELINE  = 110.0
BASE_LOAD_MW = 1182.0
BASE_GEN_MAX = 2050.0

# ── Session state ──────────────────────────────────────────────────────────────
if "running" not in st.session_state:
    st.session_state.running = True

# ── Backend data ───────────────────────────────────────────────────────────────
conn   = get_connection_status()
hist   = get_history(n=288)
latest = get_latest_state()

now_str = datetime.now().strftime("%H:%M:%S")

# ── Helper functions ───────────────────────────────────────────────────────────
def _rc(r):
    return "#e74c3c" if r >= 0.65 else "#f0a030" if r >= 0.35 else "#2ecc71"

def _kc(v, w, c, inv=False):
    if inv:
        return "kpi-crit" if v <= c else "kpi-warn" if v <= w else "kpi-ok"
    return "kpi-crit" if v >= c else "kpi-warn" if v >= w else "kpi-ok"

def _ac(a):
    return {
        "CURTAIL_LOAD": "#e74c3c", "DEFER_WORKLOAD": "#f0a030",
        "NO_ACTION": "#2ecc71", "RESTORE_BASELINE": "#2ecc71",
        "ALERT_OPERATOR": "#4ac8f0",
    }.get(a.upper(), "#a8c8a0")

def _b1_line(v): return 0.90 if v >= 90 else 0.65 if v >= 85 else 0.35 if v >= 75 else 0.10
def _b1_res(v):  return 0.90 if v <= 300 else 0.50 if v <= 400 else 0.10
def _b1_volt(v): return 0.90 if v <= 0.95 else 0.40 if v <= 0.97 else 0.05

def _dc_state(active_str: str) -> str:
    s = active_str.lower()
    if "ai_spike"    in s: return "TRAINING SPIKE"
    if "ai_dropout"  in s: return "TRAINING DROPOUT"
    if "cooling"     in s: return "COOLING CASCADE"
    if "oscillation" in s: return "OSCILLATING"
    return "NOMINAL"

def _b2_why(action, max_line, reserve, risk, v_min):
    a = action.upper()
    if a == "CURTAIL_LOAD":
        return (f"The busiest transmission line is at {max_line:.0f}% of its limit. "
                "If it hits 100% it trips automatically, forcing power onto other lines — "
                "which can cascade. Curtailing the data center by 20% brings it back to a safe range immediately.")
    if a == "DEFER_WORKLOAD":
        return (f"The grid's safety buffer is down to {reserve:.0f} MW — below the 400 MW threshold operators prefer. "
                "Delaying the data center's background computing tasks would free up ~16 MW and restore comfortable headroom.")
    if a == "ALERT_OPERATOR":
        return (f"Risk score is {risk:.2f} — elevated but not severe enough for automatic action. "
                "A human operator should assess the trend before committing to a response.")
    return (f"Reserve is {reserve:.0f} MW. Max line loading is {max_line:.1f}%. "
            f"Voltage is {v_min:.4f} pu. The grid is handling demand comfortably.")

def _reconstruct_line_pcts(dc_load, mult):
    stress = max(0, dc_load - 110) * 0.38 + (mult - 0.77) * 195
    return [round(b + (stress if i in (6, 8) else stress * 0.3), 1)
            for i, b in enumerate(BASE_LINE_PCT)]

def _send_operator_choice(choice: str):
    import json
    OPERATOR_RESP_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPERATOR_RESP_PATH.write_text(json.dumps({"choice": choice}))

def sbar(val, label, plain):
    col = _rc(val)
    return (f'<div style="padding:8px 0;border-bottom:1px solid #0a1a0a">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
            f'<div><div style="font-family:Share Tech Mono;font-size:10px;color:#4a8a4a">{label}</div>'
            f'<div style="font-family:Rajdhani;font-size:11px;color:#5a9a5a">{plain}</div></div>'
            f'<div style="font-family:Share Tech Mono;font-size:16px;color:{col};min-width:38px;text-align:right">{val:.2f}</div></div>'
            f'<div class="risk-bar-wrap" style="margin:0"><div class="risk-bar" style="width:{val*100:.1f}%;background:{col}"></div></div></div>')

# ══════════════════════════════════════════════════════════════════════════════
# OFFLINE SCREEN
# ══════════════════════════════════════════════════════════════════════════════
if conn["status"] == "OFFLINE" or latest is None:
    st.markdown(f"""
    <div class="ems-header">
      <div style="display:flex;align-items:center">
        {_LOGO_IMG}
        <div>
          <div class="ems-logo">⚡ GRIDAGENT // <span>PEPCO DC — LIVE GRID MONITOR</span></div>
          <div class="ems-sub">WASHINGTON DC · 58 SUBSTATIONS · NOMA DATA CENTER HUB</div>
        </div>
      </div>
      <div style="text-align:center">
        <div style="font-family:'Share Tech Mono';font-size:12px;color:#3a6a3a;letter-spacing:.1em">NO GRID CONNECTED</div>
      </div>
      <div style="text-align:right">
        <div class="ems-clock">{now_str}</div>
        <div class="ems-sub">WAITING FOR SIMULATION</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
                padding:80px 20px;text-align:center">
      <div style="font-family:'Share Tech Mono';font-size:48px;color:#1a3a1a;margin-bottom:16px">◌</div>
      <div style="font-family:'Share Tech Mono';font-size:16px;color:#2a6a2a;letter-spacing:.2em;margin-bottom:12px">
        NO GRID CONNECTED
      </div>
      <div style="font-family:'Rajdhani';font-size:15px;color:#4a7a4a;max-width:480px;line-height:1.6;margin-bottom:32px">
        {conn["message"]}
      </div>
      <div style="font-family:'Share Tech Mono';font-size:11px;color:#1a4a1a;
                  background:#080e10;border:1px solid #1a3a1a;padding:12px 24px;border-radius:2px;
                  letter-spacing:.08em">
        python src/run_live.py
      </div>
      <div style="font-family:'Rajdhani';font-size:12px;color:#2a4a2a;margin-top:16px">
        Dashboard will connect automatically once the simulation starts writing data.
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Still auto-refresh so it picks up the connection when it starts
    nc1, nc2 = st.columns([1, 5])
    with nc1:
        if st.button("⏸ PAUSE" if st.session_state.running else "▶ RUN"):
            st.session_state.running = not st.session_state.running
            st.rerun()

    if st.session_state.running:
        time.sleep(1.0)
        st.rerun()

    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# GRID IS CONNECTED — unpack latest state
# ══════════════════════════════════════════════════════════════════════════════
tick       = int(latest.get("tick", 0))
sim_time   = str(latest.get("sim_time", "00:00"))
day_num    = int(latest.get("day", 1))
mult       = float(latest.get("load_multiplier", 1.0))
total_load = float(latest.get("total_load_mw", 0))
reserve    = float(latest.get("reserve_mw", 0))
max_line   = float(latest.get("max_line_loading_pct", 0))
v_min      = float(latest.get("min_voltage_pu", 1.0))
dc_load    = float(latest.get("dc_noma_mw", DC_BASELINE))
risk       = float(latest.get("brain1_risk", 0))
action_needed = bool(int(latest.get("action_needed", 0)))
b2_raw     = str(latest.get("brain2_action", "no_action"))
b2         = b2_raw.upper().replace(" ", "_") if b2_raw else "NO_ACTION"
target_b2  = str(latest.get("brain2_target", "—"))
conf_raw   = str(latest.get("brain2_confidence", "low"))
conf       = conf_raw.upper() if conf_raw else "LOW"
active_str = str(latest.get("active_events", ""))
dc_state   = _dc_state(active_str)
n_viol     = int(latest.get("n_violations", 0))
ev_fired   = bool(int(latest.get("event_fired", 0)))
ev_name    = str(latest.get("event_name", ""))
ev_type    = str(latest.get("event_type", ""))
op_choice  = str(latest.get("operator_choice", ""))

# Derived Brain 1 component scores (reconstructed from metrics)
bl = _b1_line(max_line)
br = _b1_res(reserve)
bv = _b1_volt(v_min)
top_threat = max([("line", bl), ("reserve", br), ("voltage", bv)], key=lambda x: x[1])[0]

# Reconstruct violations from thresholds
pv_map = {
    TX_LINES[6]:      "This power line is carrying more than designed — risk of automatic trip.",
    "Reserve Margin": "Safety buffer is dangerously thin.",
    "Bus Voltage":    "Voltage is outside the normal safe band.",
}
violations = []
if max_line > 100: violations.append(dict(name=TX_LINES[6],      val=f"{max_line:.1f}%",   sev="CRIT"))
if reserve  < 350: violations.append(dict(name="Reserve Margin", val=f"{reserve:.0f} MW",  sev="CRIT"))
if v_min    < 0.97: violations.append(dict(name="Bus Voltage",   val=f"{v_min:.4f} pu",    sev="WARN"))

# Reconstruct per-line loading from stress model
line_pcts = _reconstruct_line_pcts(dc_load, mult)

needs_decision = action_needed and b2 not in ("NO_ACTION", "")
why = _b2_why(b2, max_line, reserve, risk, v_min)

# History DataFrame for charts
hist_df = hist if not hist.empty else pd.DataFrame()

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
conn_badge = (f'<span style="font-family:Share Tech Mono;font-size:9px;'
              f'color:{"#2ecc71" if conn["status"]=="LIVE" else "#f0a030"};'
              f'border:1px solid;padding:1px 6px;border-radius:2px;margin-left:10px">'
              f'{conn["status"]}</span>')

sys_label = "⚠  ACTION NEEDED — RESPOND IN TERMINAL" if needs_decision else "NORMAL — ALL SYSTEMS STABLE"
sys_color = "#e74c3c" if risk >= 0.65 else "#f0a030" if risk >= 0.35 else "#2ecc71"

st.markdown(f"""
<div class="ems-header">
  <div style="display:flex;align-items:center">
    {_LOGO_IMG}
    <div>
      <div class="ems-logo">⚡ GRIDAGENT // <span>PEPCO DC — LIVE GRID MONITOR</span>{conn_badge}</div>
      <div class="ems-sub">WASHINGTON DC · 58 SUBSTATIONS · NOMA DATA CENTER HUB · TICK {tick:04d}</div>
    </div>
  </div>
  <div style="text-align:center">
    <div style="font-family:'Share Tech Mono';font-size:12px;color:{sys_color};letter-spacing:.1em">{sys_label}</div>
  </div>
  <div style="text-align:right">
    <div class="ems-clock">{sim_time}</div>
    <div class="ems-sub">DAY {day_num} &nbsp;·&nbsp; {now_str}</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# KPI STRIP
# ══════════════════════════════════════════════════════════════════════════════
st.markdown(f"""<div class="kpi-strip">
<div class="kpi-block"><div class="kpi-label">Total Power Demand</div><div class="kpi-value {_kc(total_load,BASE_LOAD_MW*1.05,BASE_LOAD_MW*1.12)}">{total_load:.0f}</div><div class="kpi-unit">MEGAWATTS — CITY + DATA CENTERS</div></div>
<div class="kpi-block"><div class="kpi-label">Safety Buffer</div><div class="kpi-value {_kc(reserve,400,300,inv=True)}">{reserve:.0f}</div><div class="kpi-unit">MW SPARE CAPACITY (MIN 350)</div></div>
<div class="kpi-block"><div class="kpi-label">Busiest Power Line</div><div class="kpi-value {_kc(max_line,75,90)}">{max_line:.1f}</div><div class="kpi-unit">% OF MAX CAPACITY</div></div>
<div class="kpi-block"><div class="kpi-label">Grid Voltage</div><div class="kpi-value kpi-dim">{v_min:.4f}</div><div class="kpi-unit">PU — TARGET 0.95 – 1.05</div></div>
<div class="kpi-block"><div class="kpi-label">Data Center Load</div><div class="kpi-value {'kpi-warn' if dc_load>DC_BASELINE else 'kpi-info'}">{dc_load:.1f}</div><div class="kpi-unit">MW · {dc_state}</div></div>
<div class="kpi-block"><div class="kpi-label">AI Risk Score</div><div class="kpi-value {_kc(risk,.35,.65)}">{risk:.3f}</div><div class="kpi-unit">0 = SAFE &nbsp; 1 = CRITICAL</div></div>
<div class="kpi-block"><div class="kpi-label">Active Events</div><div class="kpi-value {'kpi-warn' if active_str else 'kpi-ok'}">{len([x for x in active_str.split('|') if x])}</div><div class="kpi-unit">DATA CENTER DISRUPTIONS</div></div>
<div class="kpi-block"><div class="kpi-label">Violations</div><div class="kpi-value {'kpi-crit' if violations else 'kpi-ok'}">{len(violations)}</div><div class="kpi-unit">OPERATING LIMITS BREACHED</div></div>
</div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# ALARM STRIP
# ══════════════════════════════════════════════════════════════════════════════
alarm_html = '<div class="alarm-strip"><span style="font-family:Share Tech Mono;font-size:9px;color:#2a5a2a;white-space:nowrap">LIVE ALARMS &nbsp;|&nbsp;</span>'
if not violations and not active_str:
    alarm_html += '<span class="alarm-item alarm-ok">✓ ALL SYSTEMS NORMAL</span>'
else:
    for v in violations:
        cls = "alarm-crit" if v["sev"] == "CRIT" else "alarm-warn"
        alarm_html += f'<span class="alarm-item {cls}">⚠ {v["name"]} AT {v["val"]}</span>'
    for ev_n in [x for x in active_str.split("|") if x]:
        alarm_html += f'<span class="alarm-item alarm-warn">⚡ {ev_n.replace("_", " ").upper()}</span>'
alarm_html += f'<span class="alarm-ts">{now_str}</span></div>'
st.markdown(alarm_html, unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# DANGER PANEL — interactive operator action buttons
# ══════════════════════════════════════════════════════════════════════════════
if needs_decision:
    plain_label, plain_explain = ACTION_PLAIN.get(b2, ("Review situation", ""))
    conf_col = {"HIGH": "#e74c3c", "MEDIUM": "#f0a030"}.get(conf, "#4ac8f0")

    # Flashing danger header
    st.markdown(f"""
    <div style="background:rgba(231,76,60,.10);border:2px solid #e74c3c;border-left:6px solid #e74c3c;
         padding:14px 20px;margin:8px 0 0;border-radius:2px;animation:blink 1.4s infinite">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:24px">
        <div style="flex:2">
          <div style="font-family:'Share Tech Mono';font-size:11px;color:#e74c3c;letter-spacing:.2em;margin-bottom:6px">
            ⚠  GRID EVENT — OPERATOR RESPONSE REQUIRED
          </div>
          <div style="font-family:'Rajdhani';font-size:20px;color:#ff8080;font-weight:700;margin-bottom:4px">
            {ev_name.replace("_", " ").title() if ev_name else "Grid stress detected"}
          </div>
          <div style="font-family:'Rajdhani';font-size:13px;color:#c8e0c0;line-height:1.6">{why}</div>
          {"<div style='font-family:Share Tech Mono;font-size:9px;color:#2ecc71;margin-top:8px;letter-spacing:.08em'>✓ OPERATOR RESPONDED: " + op_choice.upper() + "</div>" if op_choice else ""}
        </div>
        <div style="flex:1;background:rgba(231,76,60,.12);border:1px solid rgba(231,76,60,.4);
             border-radius:2px;padding:12px;text-align:center;min-width:160px">
          <div style="font-family:'Share Tech Mono';font-size:9px;color:#e74c3c;letter-spacing:.14em;margin-bottom:6px">AI RECOMMENDS</div>
          <div style="font-family:'Share Tech Mono';font-size:14px;color:#ff8080;margin-bottom:3px">{b2}</div>
          <div style="font-family:'Rajdhani';font-size:14px;color:#ffb080;font-weight:600">{plain_label}</div>
          <div style="font-family:'Share Tech Mono';font-size:9px;color:{conf_col};margin-top:6px">CONFIDENCE: {conf}</div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Action buttons — clicking writes the choice to the response file
    st.markdown('<div style="margin:6px 0 4px;font-family:Share Tech Mono;font-size:9px;color:#e74c3c;letter-spacing:.15em">SELECT RESPONSE ACTION:</div>', unsafe_allow_html=True)

    _btn_style = """
    <style>
    div[data-testid="stHorizontalBlock"] .stButton>button{width:100%!important;padding:10px 6px!important;font-size:11px!important;}
    .btn-recommend .stButton>button{background:rgba(231,76,60,.18)!important;border-color:#e74c3c!important;color:#ff8080!important;}
    .btn-defer     .stButton>button{background:rgba(240,160,48,.12)!important;border-color:#f0a030!important;color:#ffc060!important;}
    .btn-curtail   .stButton>button{background:rgba(231,76,60,.12)!important;border-color:#c0392b!important;color:#ff6060!important;}
    .btn-restore   .stButton>button{background:rgba(46,204,113,.08)!important;border-color:#2ecc71!important;color:#2ecc71!important;}
    .btn-ack       .stButton>button{background:rgba(74,200,240,.08)!important;border-color:#4ac8f0!important;color:#4ac8f0!important;}
    </style>
    """
    st.markdown(_btn_style, unsafe_allow_html=True)

    ba, bb, bc, bd, be = st.columns(5)
    with ba:
        st.markdown('<div class="btn-recommend">', unsafe_allow_html=True)
        if st.button(f"[1] {b2.replace('_',' ')}\n← AI REC", key="op_1"):
            _send_operator_choice("1")
            st.session_state.running = True
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    with bb:
        st.markdown('<div class="btn-defer">', unsafe_allow_html=True)
        if st.button("[2] DEFER\nWorkload", key="op_2"):
            _send_operator_choice("2")
            st.session_state.running = True
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    with bc:
        st.markdown('<div class="btn-curtail">', unsafe_allow_html=True)
        if st.button("[3] CURTAIL\nLoad now", key="op_3"):
            _send_operator_choice("3")
            st.session_state.running = True
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    with bd:
        st.markdown('<div class="btn-restore">', unsafe_allow_html=True)
        if st.button("[4] RESTORE\nBaseline", key="op_4"):
            _send_operator_choice("4")
            st.session_state.running = True
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    with be:
        st.markdown('<div class="btn-ack">', unsafe_allow_html=True)
        if st.button("[↵] ACKNOWLEDGE\n& Resolve", key="op_enter"):
            _send_operator_choice("")
            st.session_state.running = True
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

# ── Dashboard controls ─────────────────────────────────────────────────────────
nc1, nc4 = st.columns([1, 5])
with nc1:
    if st.button("⏸ PAUSE" if st.session_state.running else "▶ RUN"):
        st.session_state.running = not st.session_state.running
        st.rerun()
with nc4:
    age_txt = f"  ·  last update {conn['age_s']:.0f}s ago" if conn.get("age_s") is not None else ""
    st.markdown(
        f'<div style="font-family:Share Tech Mono;font-size:9px;color:#2a5a2a;padding-top:10px;letter-spacing:.1em">'
        f'GRID STATUS: <span style="color:{"#2ecc71" if conn["status"]=="LIVE" else "#f0a030"}">'
        f'{conn["status"]}</span>{age_txt} &nbsp;·&nbsp; {conn.get("total_ticks","—")} ticks recorded</div>',
        unsafe_allow_html=True,
    )

# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════
tab_live, tab_lines, tab_brain, tab_events = st.tabs([
    "  LIVE MONITOR  ", "  POWER LINES  ", "  AI AGENT  ", "  EVENT LOG  "
])

# ── TAB 1: LIVE MONITOR ───────────────────────────────────────────────────────
with tab_live:
    col_charts, col_right = st.columns([3, 1.1])

    with col_charts:
        if not hist_df.empty and "total_load_mw" in hist_df.columns:
            st.markdown('<div class="scada-card-title" style="padding:8px 0 4px;font-family:Share Tech Mono;font-size:9px;color:#2a5a2a;letter-spacing:.18em">TOTAL POWER DEMAND (MW) — LIVE TREND</div>', unsafe_allow_html=True)
            st.line_chart(
                hist_df.set_index("tick")[["total_load_mw", "dc_noma_mw"]].rename(
                    columns={"total_load_mw": "City + Data Centers (MW)", "dc_noma_mw": "Data Center Only (MW)"}
                ),
                color=["#2ecc71", "#f0a030"], height=155,
            )

            st.markdown('<div class="scada-card-title" style="padding:8px 0 4px;font-family:Share Tech Mono;font-size:9px;color:#2a5a2a;letter-spacing:.18em">SAFETY BUFFER (MW) · BUSIEST LINE LOADING (%)</div>', unsafe_allow_html=True)
            st.line_chart(
                hist_df.set_index("tick")[["reserve_mw", "max_line_loading_pct"]].rename(
                    columns={"reserve_mw": "Safety Buffer (MW)", "max_line_loading_pct": "Busiest Line (%)"}
                ),
                color=["#4ac8f0", "#e74c3c"], height=135,
            )

            st.markdown('<div class="scada-card-title" style="padding:8px 0 4px;font-family:Share Tech Mono;font-size:9px;color:#2a5a2a;letter-spacing:.18em">AI RISK SCORE (0 = SAFE · 1 = CRITICAL)</div>', unsafe_allow_html=True)
            st.line_chart(
                hist_df.set_index("tick")[["brain1_risk"]].rename(columns={"brain1_risk": "Risk Score"}),
                color=["#e74c3c"], height=110,
            )
        else:
            st.markdown('<div style="font-family:Share Tech Mono;font-size:11px;color:#2a5a2a;padding:60px 0;text-align:center">AWAITING GRID DATA — RUN python src/run_live.py</div>', unsafe_allow_html=True)

    with col_right:
        rc   = _rc(risk)
        pct  = risk * 100
        rlabel = "CRITICAL" if risk >= 0.65 else "ELEVATED" if risk >= 0.35 else "SAFE"
        plain_risk = (
            "The grid is under significant stress. Respond to Brain 2 in the terminal." if risk >= 0.65
            else "Some conditions are elevated. The agent is watching closely." if risk >= 0.35
            else "The grid is operating normally. No action needed."
        )
        st.markdown(
            f'<div class="scada-card" style="text-align:center;padding:16px 12px">'
            f'<div class="scada-card-title" style="text-align:center">GRID HEALTH</div>'
            f'<div class="risk-big" style="color:{rc}">{risk:.3f}</div>'
            f'<div style="font-family:Share Tech Mono;font-size:11px;letter-spacing:.2em;color:{rc};margin-top:4px">{rlabel}</div>'
            f'<div class="risk-bar-wrap" style="margin:10px 0 4px"><div class="risk-bar" style="width:{pct:.1f}%;background:{rc}"></div></div>'
            f'<div style="font-family:Rajdhani;font-size:12px;color:#7ab08a;margin-top:8px;line-height:1.4;text-align:left">{plain_risk}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
        dc_pct = (dc_load - 80) / 60 * 100
        dc_col = "#e74c3c" if dc_load > 130 else "#f0a030" if dc_load > DC_BASELINE else "#4ac8f0"
        plain_dc = DC_STATE_PLAIN.get(dc_state, "")
        st.markdown(
            f'<div class="scada-card" style="padding:12px">'
            f'<div class="scada-card-title">DATA CENTER STATUS</div>'
            f'<div style="font-family:Share Tech Mono;font-size:24px;color:{dc_col};line-height:1">'
            f'{dc_load:.1f} <span style="font-size:10px;color:#2a5a2a">MW</span></div>'
            f'<div style="font-family:Share Tech Mono;font-size:10px;color:{dc_col};margin-top:3px">{dc_state}</div>'
            f'<div class="risk-bar-wrap" style="margin:8px 0 4px"><div class="risk-bar" style="width:{dc_pct:.1f}%;background:{dc_col}"></div></div>'
            f'<div style="display:flex;justify-content:space-between;font-family:Share Tech Mono;font-size:9px;color:#2a5a2a;margin-bottom:8px"><span>80</span><span>110 normal</span><span>140 MW</span></div>'
            f'<div style="font-family:Rajdhani;font-size:12px;color:#7ab08a;line-height:1.4">{plain_dc}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
        viol_inner = '<div style="font-family:Share Tech Mono;font-size:10px;color:#2ecc71;padding:8px 0;text-align:center">✓ NO VIOLATIONS</div>'
        if violations:
            viol_inner = ""
            for v in violations:
                cls = "st-crit" if v["sev"] == "CRIT" else "st-warn"
                pv  = next((t for k, t in pv_map.items() if k in v["name"]), "Operating limit breached.")
                viol_inner += (
                    f'<div style="padding:6px 0;border-bottom:1px solid #0a1a0a">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center">'
                    f'<div class="oneline-name">{v["name"]}</div>'
                    f'<div style="display:flex;gap:6px;align-items:center">'
                    f'<div class="oneline-val">{v["val"]}</div>'
                    f'<div class="oneline-status {cls}">{v["sev"]}</div></div></div>'
                    f'<div style="font-family:Rajdhani;font-size:11px;color:#6a9a6a;margin-top:3px">{pv}</div></div>'
                )
        st.markdown(f'<div class="scada-card"><div class="scada-card-title">LIMIT VIOLATIONS</div>{viol_inner}</div>', unsafe_allow_html=True)

# ── TAB 2: POWER LINES ────────────────────────────────────────────────────────
with tab_lines:
    pl1, pl2 = st.columns(2)

    with pl1:
        tx_html = ""
        for i, (name, pct) in enumerate(zip(TX_LINES, line_pcts)):
            cls   = "st-crit" if pct >= 90 else "st-warn" if pct >= 75 else "st-ok"
            bar_c = "#e74c3c" if pct >= 90 else "#f0a030" if pct >= 75 else "#2ecc71"
            lbl   = "OVERLOADED" if pct >= 100 else "HIGH" if pct >= 75 else "OK"
            tx_html += (
                f'<div style="padding:5px 0;border-bottom:1px solid #0a1a0a">'
                f'<div style="display:flex;justify-content:space-between;align-items:center">'
                f'<div class="oneline-name">{name}</div>'
                f'<div style="display:flex;gap:6px;align-items:center">'
                f'<div style="font-family:Share Tech Mono;font-size:11px;color:{bar_c}">{pct:.1f}%</div>'
                f'<div class="oneline-status {cls}" style="font-size:8px">{lbl}</div></div></div>'
                f'<div style="height:3px;background:#0d1f0d;border-radius:2px;margin-top:3px;overflow:hidden">'
                f'<div style="height:100%;width:{min(100, pct):.1f}%;background:{bar_c};border-radius:2px"></div></div></div>'
            )
        st.markdown(
            f'<div class="scada-card"><div class="scada-card-title">Transmission Lines — Live Loading</div>'
            f'{tx_html}'
            f'<div class="plain-box" style="margin-top:10px"><div class="plain-title">Plain English</div>'
            f'<div class="plain-text">These are the main power highways across DC. Each bar shows how full '
            f'that highway is. Above 90% is dangerous — like a freeway at a complete standstill. '
            f'The busiest right now is at <b>{max_line:.0f}%</b>.</div></div></div>',
            unsafe_allow_html=True,
        )

    with pl2:
        gen_html = ""
        for name, p, pmax, desc in [
            ("Benning Road (Main)", 850, 1500, "Primary generator — slack bus"),
            ("Georgetown",          200, 300,  "Backup — west DC"),
            ("Buzzard Point",       150, 250,  "Backup — southwest DC"),
        ]:
            gen_html += (
                f'<div style="padding:7px 0;border-bottom:1px solid #0a1a0a">'
                f'<div style="display:flex;justify-content:space-between">'
                f'<div><div class="oneline-name">{name}</div>'
                f'<div style="font-family:Rajdhani;font-size:11px;color:#4a8a4a">{desc}</div></div>'
                f'<div style="text-align:right">'
                f'<div style="font-family:Share Tech Mono;font-size:12px;color:#c8f0c0">{p} MW</div>'
                f'<div class="oneline-status st-ok">ONLINE</div></div></div>'
                f'<div class="risk-bar-wrap" style="margin:4px 0 0">'
                f'<div class="risk-bar" style="width:{p/pmax*100:.0f}%;background:#2ecc71"></div></div></div>'
            )
        st.markdown(
            f'<div class="scada-card"><div class="scada-card-title">Power Stations</div>{gen_html}'
            f'<div style="font-family:Share Tech Mono;font-size:10px;color:#2a5a2a;padding-top:8px;'
            f'border-top:1px solid #0a1a0a;display:flex;justify-content:space-between">'
            f'<span>Total capacity</span><span style="color:#4ac8f0">2,050 MW</span></div></div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
        der_html = ""
        for name, mw, loc in [
            ("Mt Vernon Square", 15, "NE"), ("Navy Yard",    20, "SE"),
            ("Shaw",             10, "NW"), ("NoMa",          12, "NE"),
            ("Capitol Hill",      8, "SE"), ("Howard Univ",    5, "NW"),
        ]:
            der_html += (
                f'<div class="oneline-row">'
                f'<div><div class="oneline-name">{name}</div>'
                f'<div style="font-family:Rajdhani;font-size:11px;color:#3a6a3a">{loc} DC</div></div>'
                f'<div style="display:flex;gap:6px;align-items:center">'
                f'<div class="oneline-val">{mw} MW</div>'
                f'<div class="oneline-status st-ok">ON</div></div></div>'
            )
        st.markdown(
            f'<div class="scada-card"><div class="scada-card-title">Local Renewables (Solar/DER)</div>'
            f'{der_html}'
            f'<div style="font-family:Share Tech Mono;font-size:9px;color:#2a5a2a;padding-top:6px;'
            f'border-top:1px solid #0a1a0a">70 MW total — reduces load on main generators</div></div>',
            unsafe_allow_html=True,
        )

# ── TAB 3: AI AGENT ───────────────────────────────────────────────────────────
with tab_brain:
    ab1, ab2 = st.columns([1, 1.4])

    with ab1:
        threat_plain = {
            "line":    "A transmission line is the biggest concern.",
            "reserve": "Low spare capacity is the main risk.",
            "voltage": "Voltage is outside the normal band.",
        }.get(top_threat, "No dominant threat — grid is stable.")

        st.markdown(
            f'<div class="scada-card"><div class="scada-card-title">Brain 1 — Automated Risk Scoring</div>'
            f'<div style="font-family:Rajdhani;font-size:12px;color:#5a9a6a;margin-bottom:10px;line-height:1.4">'
            f'Scores three grid conditions every tick on a 0–1 scale. Above 0.65 triggers Brain 2 to recommend action.</div>'
            f'{sbar(bl, "TRANSMISSION LINE RISK", "How close are lines to their limit?")}'
            f'{sbar(br, "RESERVE CAPACITY RISK",  "How thin is our safety buffer?")}'
            f'{sbar(bv, "VOLTAGE STABILITY RISK", "Is voltage in the safe band?")}'
            f'<div style="margin-top:10px;padding-top:8px;border-top:1px solid #0a1a0a">'
            f'<div style="font-family:Share Tech Mono;font-size:9px;color:#2a5a2a;letter-spacing:.1em">BIGGEST CONCERN</div>'
            f'<div style="font-family:Share Tech Mono;font-size:14px;color:{_rc(risk)};margin-top:2px">{top_threat.upper()}</div>'
            f'<div style="font-family:Rajdhani;font-size:12px;color:#5a9a6a;margin-top:3px">{threat_plain}</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    with ab2:
        ac           = _ac(b2)
        conf_col     = {"HIGH": "#e74c3c", "MEDIUM": "#f0a030"}.get(conf, "#4ac8f0")
        plain_label, plain_explain = ACTION_PLAIN.get(b2, ("", ""))
        conf_plain   = {
            "HIGH":   "Very confident — data strongly supports this.",
            "MEDIUM": "Reasonably confident — monitoring.",
            "LOW":    "Uncertain — human review recommended.",
        }.get(conf, "")

        st.markdown(
            f'<div class="scada-card"><div class="scada-card-title">Brain 2 — AI Reasoning Agent (Claude)</div>'
            f'<div style="font-family:Rajdhani;font-size:12px;color:#5a9a6a;margin-bottom:10px;line-height:1.4">'
            f"Reads Brain 1's scores, considers demand trends and market signals, and reasons about what to do "
            f"next — like a senior operator who never sleeps.</div>"
            f'<div class="b2-box"><div style="display:flex;justify-content:space-between;align-items:flex-start">'
            f'<div><div style="font-family:Share Tech Mono;font-size:9px;color:#2a4a6a;letter-spacing:.12em;margin-bottom:4px">RECOMMENDED ACTION</div>'
            f'<div style="font-family:Share Tech Mono;font-size:14px;color:{ac}">{b2}</div>'
            f'<div style="font-family:Rajdhani;font-size:15px;color:#a8d0f0;margin-top:3px;font-weight:600">→ {plain_label}</div>'
            f'<div style="font-family:Share Tech Mono;font-size:10px;color:#2a6a8a;margin-top:2px">TARGET: {target_b2}</div></div>'
            f'<div style="text-align:right">'
            f'<div style="font-family:Share Tech Mono;font-size:9px;color:#2a4a6a;letter-spacing:.1em">CONFIDENCE</div>'
            f'<div style="font-family:Share Tech Mono;font-size:18px;color:{conf_col}">{conf}</div>'
            f'<div style="font-family:Rajdhani;font-size:10px;color:#5a8a9a;max-width:120px;text-align:right;margin-top:2px">{conf_plain}</div>'
            f'</div></div></div>'
            f'<div style="font-family:Share Tech Mono;font-size:9px;color:#2a4a6a;letter-spacing:.1em;margin-top:10px;margin-bottom:4px">WHAT THIS MEANS</div>'
            f'<div style="font-family:Rajdhani;font-size:13px;color:#7ab0c8;line-height:1.6;padding:10px;background:rgba(42,122,170,.05);border:1px solid #0a2030;border-radius:2px">{plain_explain}</div>'
            f'<div style="font-family:Share Tech Mono;font-size:9px;color:#2a4a6a;letter-spacing:.1em;margin-top:10px;margin-bottom:4px">TECHNICAL REASONING</div>'
            f'<div style="font-family:Rajdhani;font-size:12px;color:#5a8a9a;line-height:1.5;padding:8px;background:rgba(0,0,0,.2);border-radius:2px">{why}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
    st.markdown('<div style="font-family:Share Tech Mono;font-size:9px;color:#2a5a2a;letter-spacing:.18em;padding:4px 0">RECENT AGENT DECISIONS</div>', unsafe_allow_html=True)

    log_inner = ""
    if not hist_df.empty and "brain2_triggered" in hist_df.columns:
        agent_rows = hist_df[hist_df["brain2_triggered"] == 1].tail(20)
        for _, row in agent_rows.iloc[::-1].iterrows():
            a2   = str(row.get("brain2_action", "")).upper().replace(" ", "_")
            ac2  = _ac(a2)
            pl2  = ACTION_PLAIN.get(a2, ("", ""))[0]
            t2   = str(row.get("sim_time", ""))
            tk2  = int(row.get("tick", 0))
            tgt2 = str(row.get("brain2_target", "—"))
            rsk2 = float(row.get("brain1_risk", 0))
            log_inner += (
                f'<div class="evlog-row">'
                f'<div class="evlog-tick">{t2}<br>T:{tk2:04d}</div>'
                f'<div class="evlog-dot" style="background:{ac2}"></div>'
                f'<div><div class="evlog-msg"><span style="color:{ac2}">{a2}</span> → {tgt2} · risk {rsk2:.3f}</div>'
                f'<div style="font-family:Rajdhani;font-size:11px;color:#4a7a5a">{pl2}</div></div></div>'
            )
    if not log_inner:
        log_inner = '<div style="font-family:Share Tech Mono;font-size:10px;color:#2a5a2a;padding:16px 0;text-align:center">NO BRAIN 2 INTERVENTIONS YET</div>'

    st.markdown(f'<div class="scada-card"><div class="log-scroll">{log_inner}</div></div>', unsafe_allow_html=True)

# ── TAB 4: EVENT LOG ──────────────────────────────────────────────────────────
with tab_events:
    el1, el2 = st.columns(2)
    type_colors = {
        "ai_training_spike":   "#f0a030",
        "ai_training_dropout": "#e74c3c",
        "cooling_cascade":     "#f0a030",
        "load_oscillation":    "#4ac8f0",
        "operator":            "#2ecc71",
    }

    with el1:
        st.markdown('<div style="font-family:Share Tech Mono;font-size:9px;color:#2a5a2a;letter-spacing:.18em;padding:4px 0">EVENTS RECORDED FROM LIVE LOG</div>', unsafe_allow_html=True)
        sched_html = ""
        if not hist_df.empty and "event_fired" in hist_df.columns:
            ev_rows = hist_df[hist_df["event_fired"] == 1]
            if ev_rows.empty:
                sched_html = '<div style="font-family:Share Tech Mono;font-size:10px;color:#2a5a2a;padding:20px 0;text-align:center">NO EVENTS FIRED YET</div>'
            else:
                for _, row in ev_rows.iterrows():
                    ename  = str(row.get("event_name", ""))
                    etype  = str(row.get("event_type", "")).lower()
                    etime  = str(row.get("sim_time", ""))
                    etick  = int(row.get("tick", 0))
                    eday   = int(row.get("day", 1))
                    op_ch  = str(row.get("operator_choice", ""))
                    ec     = type_colors.get(etype, "#a8c8a0")
                    op_str = f'  ·  operator: {op_ch.upper()}' if op_ch else ""
                    sched_html += (
                        f'<div style="padding:10px 0;border-bottom:1px solid #0a1a0a">'
                        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px">'
                        f'<div><div style="font-family:Share Tech Mono;font-size:9px;color:{ec};letter-spacing:.1em">{etype.replace("_"," ").upper()}</div>'
                        f'<div style="font-family:Rajdhani;font-size:14px;color:#a8c8a0;font-weight:600;margin-top:2px">{ename.replace("_"," ").title()}</div>'
                        f'<div style="font-family:Share Tech Mono;font-size:9px;color:#2a5a2a;margin-top:2px">'
                        f'Day {eday} · {etime} · Tick {etick:04d}{op_str}</div></div>'
                        f'<div class="oneline-status st-warn" style="white-space:nowrap;font-size:9px">FIRED</div>'
                        f'</div></div>'
                    )
        else:
            sched_html = '<div style="font-family:Share Tech Mono;font-size:10px;color:#2a5a2a;padding:20px 0;text-align:center">NO DATA YET</div>'

        st.markdown(f'<div class="scada-card">{sched_html}</div>', unsafe_allow_html=True)

    with el2:
        st.markdown('<div style="font-family:Share Tech Mono;font-size:9px;color:#2a5a2a;letter-spacing:.18em;padding:4px 0">LIVE ALARM & OPERATOR RESPONSE LOG</div>', unsafe_allow_html=True)
        rt_inner = ""
        if not hist_df.empty and "event_fired" in hist_df.columns:
            ev_rows = hist_df[hist_df["event_fired"] == 1].tail(35)
            for _, row in ev_rows.iloc[::-1].iterrows():
                ename  = str(row.get("event_name", "")).replace("_", " ")
                etime  = str(row.get("sim_time", ""))
                etick  = int(row.get("tick", 0))
                op_ch  = str(row.get("operator_choice", ""))
                dot_c  = "#f0a030"
                rt_inner += (
                    f'<div class="evlog-row">'
                    f'<div class="evlog-tick">{etime}<br>T:{etick:04d}</div>'
                    f'<div class="evlog-dot" style="background:{dot_c}"></div>'
                    f'<div><div class="evlog-msg"><span style="color:{dot_c}">[FIRED]</span> &nbsp;{ename}</div>'
                    + (f'<div style="font-family:Share Tech Mono;font-size:9px;color:#2ecc71">operator → {op_ch.upper()}</div>' if op_ch else '')
                    + f'</div></div>'
                )
        if not rt_inner:
            rt_inner = '<div style="font-family:Share Tech Mono;font-size:10px;color:#2a5a2a;padding:20px 0;text-align:center">RUNNING — NO EVENTS FIRED YET</div>'
        st.markdown(f'<div class="scada-card"><div class="log-scroll" style="height:420px">{rt_inner}</div></div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR — OPERATOR CHAT (always visible regardless of active tab)
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("""
    <style>
    [data-testid="stSidebar"]{background:#060e08!important;border-right:1px solid #1a4a22!important;min-width:340px!important;max-width:340px!important;}
    [data-testid="stSidebar"] .stChatMessage{background:transparent!important;}
    [data-testid="stSidebar"] [data-testid="stChatMessageContent"]{font-family:'Rajdhani'!important;font-size:13px!important;}
    [data-testid="stSidebar"] .stChatInputContainer{border-top:1px solid #1a4a22!important;background:#060e08!important;}
    [data-testid="stSidebar"] .stChatInputContainer textarea{background:#080e10!important;color:#a8c8a0!important;font-family:'Share Tech Mono'!important;font-size:11px!important;border:1px solid #1a4a22!important;}
    </style>
    """, unsafe_allow_html=True)

    st.markdown(
        '<div style="font-family:Share Tech Mono;font-size:10px;color:#2ecc71;'
        'letter-spacing:.18em;text-transform:uppercase;padding:10px 0 4px;'
        'border-bottom:1px solid #1a4a22;margin-bottom:10px">'
        '⚡ AI OPERATOR ASSISTANT</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="font-family:Rajdhani;font-size:11px;color:#4a8a5a;line-height:1.5;margin-bottom:10px">'
        'Live grid Q&amp;A — re-reads the latest tick on every turn.</div>',
        unsafe_allow_html=True,
    )

    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    if "chat_busy" not in st.session_state:
        st.session_state.chat_busy = False

    # Quick-fire suggestion buttons
    suggestions = [
        "What is the grid doing right now?",
        "Why was Brain 2's last action recommended?",
        "What's our biggest risk this hour?",
        "Explain the active event in plain English.",
    ]
    for i, q in enumerate(suggestions):
        if st.button(q, key=f"chat_sug_{i}", disabled=st.session_state.chat_busy, use_container_width=True):
            st.session_state.chat_messages.append({"role": "user", "content": q})
            st.session_state.chat_busy = True
            st.rerun()

    st.markdown('<div style="height:4px"></div>', unsafe_allow_html=True)

    # Conversation history
    chat_container = st.container(height=420)
    with chat_container:
        if not st.session_state.chat_messages:
            st.markdown(
                '<div style="font-family:Share Tech Mono;font-size:10px;color:#2a5a2a;'
                'padding:40px 0;text-align:center;letter-spacing:.1em">'
                'ASK A QUESTION ABOVE<br>OR TYPE BELOW</div>',
                unsafe_allow_html=True,
            )
        else:
            for msg in st.session_state.chat_messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

        if st.session_state.chat_busy:
            with st.chat_message("assistant"):
                with st.spinner("Reading grid state…"):
                    try:
                        reply = operator_chat(st.session_state.chat_messages)
                    except Exception as exc:
                        reply = f"⚠ Chat error: {exc}"
                st.markdown(reply)
            st.session_state.chat_messages.append({"role": "assistant", "content": reply})
            st.session_state.chat_busy = False

    prompt = st.chat_input("Ask the AI about the grid…", key="sidebar_chat_input")
    if prompt:
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        st.session_state.chat_busy = True
        st.rerun()

    st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)
    if st.button("CLEAR CONVERSATION", key="chat_clear", use_container_width=True):
        st.session_state.chat_messages = []
        st.rerun()

    st.markdown(
        '<div style="font-family:Share Tech Mono;font-size:8px;color:#1a4a2a;'
        'padding-top:8px;border-top:1px solid #0a2010;letter-spacing:.08em">'
        'TIP: HIT ⏸ PAUSE ABOVE IF AUTO-REFRESH INTERRUPTS TYPING.</div>',
        unsafe_allow_html=True,
    )

# ══════════════════════════════════════════════════════════════════════════════
# AUTO-REFRESH — poll until a new tick is written, then rerun
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.running and not st.session_state.get("chat_busy", False):
    _deadline = time.time() + 10.0  # fall-through after 10 s to refresh status
    while time.time() < _deadline:
        time.sleep(0.5)
        _new = get_latest_state()
        _new_tick = int(_new.get("tick", -1)) if _new else -1
        if _new_tick != tick:
            break
    st.rerun()
