"""
Builds simulation electric grid from config
=============================================
Ties together grid, power flow engine, and optimization agent.
"""

# ── Variable legend ───────────────────────────────────────────────────────────
# vn_kv          Nominal voltage in kilovolts (kV)
# p_mw           Active (real) power in megawatts (MW)
# q_mvar         Reactive power in megavolt-amperes reactive (MVAR)
# sn_mva         Rated apparent power in megavolt-amperes (MVA)
# r_ohm_per_km   Line resistance per kilometre (Ω/km)
# x_ohm_per_km   Line reactance per kilometre (Ω/km)
# c_nf_per_km    Line capacitance per kilometre (nF/km)
# max_i_ka       Maximum current rating in kiloamperes (kA)
# vm_pu          Voltage magnitude in per-unit (p.u.)
# va_degree      Voltage angle in degrees (°)
# p_min_mw       Minimum generator real power output (MW)
# p_max_mw       Maximum generator real power output (MW)
# hv / lv        High-voltage / low-voltage side of a transformer
# fb / tb        From-bus / to-bus endpoints of a line
# ──────────────────────────────────────────────────────────────────────────────

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import Dict, List, Optional, Tuple

_CONFIG_PATH = Path(__file__).parent / "config" / "grid_config.json"
with open(_CONFIG_PATH) as _f:
    _CFG = json.load(_f)

import pandapower as pp
from simulator.network import GridNetwork
from simulator.power_flow import PowerFlowEngine
# Main simulation environment generated from config
class SimulationEnvironment:
   
    # Initialize the simulation environment
    def __init__(self, grid_name: str = _CFG["grid_name"]):
        self.grid_name = grid_name
        self.grid: Optional[GridNetwork] = None
        self.pf_engine: Optional[PowerFlowEngine] = None

        self.scenario_history: List[Dict] = []
        self.current_time_step = 0

        # Tracks any active injected event; checked by the external agent loop
        self.active_event: Optional[Dict] = None
    
    # Grid simulation config - every network element can be found in the config 
    def build_grid(self):
        from simulator.network import (
            BusSpec, LineSpec, GeneratorSpec, LoadSpec,
            FlexibleLoadSpec, StaticGeneratorSpec,
        )

        self.grid = GridNetwork(name=self.grid_name)
        net = self.grid.net

        # ── Transmission substations (115 kV) ─────────────────────────────────
        for name, zone in _CFG["transmission_substations"]:
            self.grid.add_bus(BusSpec(name=name, vn_kv=115.0, zone=zone))

        # ── Distribution substations (13.8 kV) ────────────────────────────────
        for name, zone in _CFG["distribution_substations"]:
            self.grid.add_bus(BusSpec(name=name, vn_kv=13.8, zone=zone))

        # ── Transmission lines (115 kV, ACSR overhead, 200 MVA) ───────────────
        _r, _x, _c, sn_mva = _CFG["line_params"]
        for lname, fb, tb, km in _CFG["transmission_lines"]:
            self.grid.add_line(LineSpec(lname, fb, tb, km, _r, _x, _c, sn_mva))

        # ── 115 / 13.8 kV transformers ────────────────────────────────────────
        for hv, lv, sn_mva in _CFG["transformers"]:
            hv_idx = net.bus[net.bus["name"] == hv].index[0]
            lv_idx = net.bus[net.bus["name"] == lv].index[0]
            pp.create_transformer_from_parameters(
                net,
                hv_bus=hv_idx,
                lv_bus=lv_idx,
                sn_mva=float(sn_mva),
                vn_hv_kv=115.0,
                vn_lv_kv=13.8,
                vkr_percent=0.3,
                vk_percent=8.0,
                pfe_kw=25.0,
                i0_percent=0.08,
                name=f"TR_{lv}",
            )

        # ── Generators (PJM ties) ──────────────────────────────────────────────
        for gname, bus, p_mw, slack, p_max in _CFG["generators"]:
            self.grid.add_generator(GeneratorSpec(
                name=gname, bus=bus, p_mw=p_mw, slack=slack,
                p_min_mw=0.0, p_max_mw=p_max,
            ))

        # ── Static loads (~1,182 MW total peak) ───────────────────────────────
        for lname, bus, p_mw in _CFG["static_loads"]:
            self.grid.add_load(LoadSpec(name=lname, bus=bus, p_mw=p_mw))

        # ── DER — 70 MW, Mount Vernon / Navy Yard zone ────────────────────────
        for dname, bus, p_mw in _CFG["der_units"]:
            self.grid.add_static_gen(StaticGeneratorSpec(name=dname, bus=bus, p_mw=p_mw))

        # ── Flexible load — NoMa data-center hub ──────────────────────────────
        self.flex_load = self.grid.add_flexible_load(FlexibleLoadSpec(**_CFG["flexible_load"]))

        print(f"[ENV] Grid '{self.grid_name}' built:")
        print(f"      {len(net.bus):>3} buses  ({len(net.bus[net.bus['vn_kv']==115.0])} transmission, {len(net.bus[net.bus['vn_kv']==13.8])} distribution)")
        print(f"      {len(net.line):>3} transmission lines")
        print(f"      {len(net.trafo):>3} transformers (115/13.8 kV)")
        print(f"      {len(net.gen):>3} generators  ({net.gen['p_mw'].sum():.0f} MW dispatched)")
        print(f"      {len(net.load):>3} static loads ({net.load['p_mw'].sum():.0f} MW peak)")
        print(f"      {len(net.sgen):>3} DER units   ({net.sgen['p_mw'].sum():.0f} MW)")
    
    # Initialize the power flow engine and run the first power flow
    def initialize(self):
        if self.grid is None:
            raise RuntimeError("Call build_grid() first")

        self.pf_engine = PowerFlowEngine(self.grid)

        success = self.pf_engine.run()
        if not success:
            raise RuntimeError("Initial power flow failed to converge")

        print("[ENV] Initialization complete. Initial power flow converged.")
    
    def step(self) -> Dict:

        if self.grid is None or self.pf_engine is None:
            raise RuntimeError("Call initialize() first")
        
        step_result = {
            'time_step': self.current_time_step,
            'grid_state': None,
            'pf_report': None,
        }
        
        # Power flow
        pf_success = self.pf_engine.run()
        if not pf_success:
            print(f"[STEP {self.current_time_step}] Power flow FAILED")
            step_result['converged'] = False
            return step_result
        
        # Generate power flow report
        pf_report = self.pf_engine.generate_report()
        step_result['pf_report'] = pf_report
        
        # Grid state summary
        state = self.grid.get_state_summary()
        step_result['grid_state'] = state
        
        step_result['converged'] = True
        
        # Log to history
        self.scenario_history.append(step_result)
        self.current_time_step += 1
        
        return step_result
    
    # Run the simulation for N time steps, returning the full history
    def run_scenario(self, steps: int = 24, print_reports: bool = False) -> List[Dict]:
        print(f"\n[ENV] Running scenario for {steps} steps...")

        for _ in range(steps):
            result = self.step()

            if not result['converged']:
                print(f"[SCENARIO] Stopped at step {self.current_time_step} (convergence failure)")
                break
            
            if print_reports:
                self.pf_engine.print_report()
        
        print(f"[ENV] Scenario complete. {self.current_time_step} steps executed.")
        return self.scenario_history
    
    # Get summary statistics across all steps in the scenario
    def get_scenario_summary(self) -> Dict:
        if not self.scenario_history:
            return {'message': 'No scenario history'}
        
        summary = {
            'total_steps': len(self.scenario_history),
            'converged_steps': sum(1 for s in self.scenario_history if s.get('converged', False)),
            'peak_generation_mw': max(s['grid_state']['total_gen_mw'] for s in self.scenario_history),
            'peak_load_mw': max(s['grid_state']['total_load_mw'] for s in self.scenario_history),
            'max_line_loading_pct': max(
                s['pf_report']['summary'].get('max_line_loading_pct', 0.0)
                    for s in self.scenario_history
                if s.get('pf_report')
            ),
            'violations_recorded': sum(
                len(s['pf_report']['violations'])
                for s in self.scenario_history
                if s.get('pf_report')
            ),
        }
        
        return summary
    
    # Export scenario history to a JSON file
    def export_history_json(self, filepath: str):
        with open(filepath, 'w') as f:
            json.dump(self.scenario_history, f, indent=2)
        print(f"[ENV] Exported scenario history to {filepath}")

    # Inject a persistent load spike on a named load.
    # Unlike EventScheduler events this does not auto-expire — it stays on
    # the grid until resolve_event() is called. run_live.py calls this when
    # a scheduled problem fires, and calls resolve_event() when it clears.
    def inject_event(self, target_load: str, extra_mw: float, label: str = "LOAD_SPIKE") -> None:
        if self.grid is None:
            raise RuntimeError("Call build_grid() first")
        net = self.grid.net
        mask = net.load["name"] == target_load
        if not mask.any():
            raise ValueError(f"Load '{target_load}' not found in network")
        load_idx = net.load[mask].index[0]
        original_mw = float(net.load.loc[load_idx, "p_mw"])
        net.load.loc[load_idx, "p_mw"] = original_mw + extra_mw
        self.active_event = {
            "label":       label,
            "target_load": target_load,
            "load_idx":    load_idx,
            "original_mw": original_mw,
            "extra_mw":    extra_mw,
        }
        print(f"[EVENT] {label} injected on '{target_load}': "
              f"{original_mw:.1f} → {original_mw + extra_mw:.1f} MW (+{extra_mw} MW)")

    # Revert the active injected event and clear event state
    def resolve_event(self) -> None:
        if self.active_event is None:
            print("[EVENT] No active event to resolve.")
            return
        net = self.grid.net
        idx = self.active_event["load_idx"]
        net.load.loc[idx, "p_mw"] = self.active_event["original_mw"]
        print(f"[EVENT] Resolved '{self.active_event['target_load']}' — "
              f"load restored to {self.active_event['original_mw']:.1f} MW")
        self.active_event = None

    # Debug method to ensure that the grid has all of its items
    # Checks with the .json item list to ensure that all of the items are present
    def def_check(self):
        if self.grid is None:
            print("[DEBUG] Grid not built — call build_grid() first.")
            return

        net = self.grid.net
        sep = "─" * 60

        print(f"\n{sep}")
        print(f"  DEBUG CHECK — {self.grid.name}")
        print(sep)

        # ── 1. Topology counts ────────────────────────────────────────────────
        n_tx   = len(net.bus[net.bus["vn_kv"] == 115.0])
        n_dist = len(net.bus[net.bus["vn_kv"] == 13.8])
        print(f"\n[1] Topology")
        print(f"    Buses        : {len(net.bus):>3}  (tx={n_tx}, dist={n_dist})  expected 58")
        print(f"    TX lines     : {len(net.line):>3}  expected 10")
        print(f"    Transformers : {len(net.trafo):>3}  expected 51")
        print(f"    Generators   : {len(net.gen):>3}  expected 3")
        print(f"    Static loads : {len(net.load):>3}  expected 51")
        print(f"    DER (sgen)   : {len(net.sgen):>3}  expected 6  ({net.sgen['p_mw'].sum():.0f} MW)")

        ok = (len(net.bus) == 58 and len(net.line) == 10 and
              len(net.trafo) == 51 and len(net.gen) == 3 and len(net.load) == 51)
        print(f"    {'✓ Counts correct' if ok else '✗ Count mismatch — check build_grid()'}")

        # ── 2. Power flow ─────────────────────────────────────────────────────
        print(f"\n[2] Power Flow")
        try:
            import pandapower as pp
            pp.runpp(net, check_convergence=True, numba=False)
            print(f"    ✓ Converged")
        except Exception as exc:
            print(f"    ✗ FAILED: {exc}")
            print(sep)
            return

        # ── 3. Voltage check ──────────────────────────────────────────────────
        v_min_pu = self.grid.constraints["voltage_min_pu"]
        v_max_pu = self.grid.constraints["voltage_max_pu"]
        voltages  = net.res_bus["vm_pu"]
        low_buses  = net.bus[voltages < v_min_pu]["name"].tolist()
        high_buses = net.bus[voltages > v_max_pu]["name"].tolist()

        print(f"\n[3] Voltage  (limits: {v_min_pu}–{v_max_pu} p.u.)")
        print(f"    Range : {voltages.min():.4f} – {voltages.max():.4f} p.u.")
        if low_buses:
            print(f"    ✗ Under-voltage buses : {low_buses}")
        elif high_buses:
            print(f"    ✗ Over-voltage buses  : {high_buses}")
        else:
            print(f"    ✓ All buses within limits")

        # ── 4. Transformer loading ────────────────────────────────────────────
        print(f"\n[4] Transformer Loading")
        if net.trafo.empty or net.res_trafo.empty:
            print(f"    (no transformers)")
        else:
            t_loading = net.res_trafo["loading_percent"]
            overloaded = net.trafo[t_loading > 100]["name"].tolist()
            print(f"    Max loading : {t_loading.max():.1f}%  (mean {t_loading.mean():.1f}%)")
            if overloaded:
                print(f"    ✗ Overloaded transformers : {overloaded}")
            else:
                print(f"    ✓ No transformer overloads")

        # ── 5. Transmission line loading ─────────────────────────────────────
        print(f"\n[5] Transmission Line Loading")
        if net.line.empty or net.res_line.empty:
            print(f"    (no lines)")
        else:
            l_loading = net.res_line["loading_percent"]
            print(f"    Max loading : {l_loading.max():.1f}%  (mean {l_loading.mean():.1f}%)")
            for idx in l_loading.nlargest(3).index:
                name = net.line.loc[idx, "name"]
                pct  = net.res_line.loc[idx, "loading_percent"]
                print(f"    {name:<35} {pct:>6.1f}%")

        # ── 6. Top-5 loaded buses ─────────────────────────────────────────────
        print(f"\n[6] Top-5 Buses by Load Draw (MW)")
        bus_p = net.res_bus["p_mw"].abs()
        for idx in bus_p.nlargest(5).index:
            bname = net.bus.loc[idx, "name"]
            p_mw  = net.res_bus.loc[idx, "p_mw"]
            v_pu  = net.res_bus.loc[idx, "vm_pu"]
            print(f"    {bname:<25} {p_mw:>8.1f} MW   {v_pu:.4f} p.u.")

        print(f"\n{sep}\n")


# ============================================================================
# EXAMPLE: How to use the SimulationEnvironment
# ============================================================================

# Example workflow: build grid, run power flow, run agent scenario
def example_usage():
    print("\n" + "="*70)
    print("EXAMPLE: Grid Simulation Workflow")
    print("="*70)
    
    # Create environment
    env = SimulationEnvironment()
    
    # Build grid (uses placeholder 3-bus grid)
    env.build_grid()
    
    # Initialize (compile power flow engine, agent)
    env.initialize()
    
    # Run scenario for 24 hours
    results = env.run_scenario(steps=24, apply_agent=True, print_reports=False)
    
    # Print summary
    summary = env.get_scenario_summary()
    print("\nScenario Summary:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    
    # Export to JSON (for dashboard/analysis)
    env.export_history_json('/tmp/scenario_history.json')
    
    print("\n" + "="*70)


if __name__ == "__main__":
    env = SimulationEnvironment()
    env.build_grid()
    env.debug_check()