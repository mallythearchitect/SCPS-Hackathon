"""
Grid Network Definition
========================
Core simulation objects: buses, lines, generators, loads, flexible loads, transformers.
This module defines the static topology and dynamic state of the electric grid.

Usage:
    from simulator.network import GridNetwork
    grid = GridNetwork()
"""

# ── Variable legend ───────────────────────────────────────────────────────────
# vn_kv          Nominal voltage in kilovolts (kV)
# p_mw           Active (real) power in megawatts (MW)
# q_mvar         Reactive power in megavolt-amperes reactive (MVAR)
# sn_mva         Rated apparent power in megavolt-amperes (MVA)
# _r Line resistance per kilometre (Ω/km)
# _x Line reactance per kilometre (Ω/km)
# _c Line capacitance per kilometre (nF/km)
# max_i_ka       Maximum current rating in kiloamperes (kA)
# vm_pu          Voltage magnitude in per-unit (p.u.)
# va_degree      Voltage angle in degrees (°)
# p_min_mw       Minimum generator real power output (MW)
# p_max_mw       Maximum generator real power output (MW)
# hv / lv        High-voltage / low-voltage side of a transformer
# fb / tb        From-bus / to-bus endpoints of a line
# ──────────────────────────────────────────────────────────────────────────────

import math
import pandapower as pp
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
 
 
# Specification for a bus (substation)
@dataclass
class BusSpec:
    name: str
    vn_kv: float
    bus_type: str = "b"  # "b" = PQ bus, "n" = slack bus (reference), "m" = PV bus
    zone: str = "default"
    
    
# Specification for a transmission/distribution line
@dataclass
class LineSpec:
    name: str
    from_bus: str
    to_bus: str
    length_km: float
    _r: float
    _x: float
    _c: float
    sn_mva: float  # Apparent power (thermal capacity)
    
    
# Specification for a generator (power plant or renewable source)
@dataclass
class GeneratorSpec:
    name: str
    bus: str
    p_mw: float
    q_mvar: Optional[float] = None
    slack: bool = False
    p_min_mw: float = 0.0
    p_max_mw: Optional[float] = None
    
    
# Specification for a static load (city, factory, home)
@dataclass
class LoadSpec:
    name: str
    bus: str
    p_mw: float
    q_mvar: float = 0.0
    
    
# Specification for a flexible load (data center, EV charging, etc.)
@dataclass
class FlexibleLoadSpec:
    name: str
    bus: str
    baseline_mw: float  # Baseline demand (constant load)
    min_mw: float  # Minimum load (cannot go below)
    max_mw: float  # Maximum load (hard limit)
    deferrable_pct: float = 0.2  # What percentage of load can be deferred (0.0-1.0)
    defer_window_hours: int = 4  # Time window to defer load (in hours)
    
    
# Specification for a static generator (renewable DER like solar)
@dataclass
class StaticGeneratorSpec:
    name: str
    bus: str
    p_mw: float
    q_mvar: float = 0.0
 
 
# Main grid network class — wraps pandapower with domain abstractions.
# Stores topology, components (generators/loads/DER), live state, and constraint thresholds.
class GridNetwork:

    # Initialize an empty pandapower network
    def __init__(self, name: str = "Default Grid"):
        self.name = name
        self.net = pp.create_empty_network(name=name)
        
        # Metadata for tracking components
        self.bus_specs: Dict[str, BusSpec] = {}
        self.line_specs: Dict[str, LineSpec] = {}
        self.gen_specs: Dict[str, GeneratorSpec] = {}
        self.load_specs: Dict[str, LoadSpec] = {}
        self.flex_load_specs: Dict[str, FlexibleLoadSpec] = {}
        self.sgen_specs: Dict[str, StaticGeneratorSpec] = {}
        
        # Constraint thresholds
        self.constraints = {
            "line_loading_max_pct": 100.0,  # Line thermal limit (%)
            "voltage_min_pu": 0.95,  # Minimum voltage (per-unit)
            "voltage_max_pu": 1.05,  # Maximum voltage (per-unit)
            "frequency_nominal_hz": 60.0,
            "frequency_tolerance_hz": 0.2,
            "reserve_margin_mw": 350.0,  # Minimum spinning reserve
        }
        
    # Add a bus (substation) to the network, returns bus index
    def add_bus(self, spec: BusSpec) -> int:
        bus_idx = pp.create_bus(
            self.net,
            name=spec.name,
            vn_kv=spec.vn_kv,
            type=spec.bus_type,
            zone=spec.zone
        )
        self.bus_specs[spec.name] = spec
        return bus_idx
    
    # Add a transmission/distribution line to the network, returns line index
    def add_line(self, spec: LineSpec) -> int:
        from_idx = self.net.bus[self.net.bus['name'] == spec.from_bus].index[0]
        to_idx = self.net.bus[self.net.bus['name'] == spec.to_bus].index[0]
        
        # sn_mva → max_i_ka using the from-bus nominal voltage
        vn_kv = self.net.bus.loc[from_idx, "vn_kv"]
        max_i_ka = spec.sn_mva / (math.sqrt(3) * vn_kv)

        line_idx = pp.create_line_from_parameters(
            self.net,
            from_bus=from_idx,
            to_bus=to_idx,
            length_km=spec.length_km,
            r_ohm_per_km=spec._r,
            x_ohm_per_km=spec._x,
            c_nf_per_km=spec._c,
            max_i_ka=max_i_ka,
            name=spec.name,
        )
        self.line_specs[spec.name] = spec
        return line_idx
    
    # Add a generator to the network, returns generator index
    def add_generator(self, spec: GeneratorSpec) -> int:
        bus_idx = self.net.bus[self.net.bus['name'] == spec.bus].index[0]
        
        gen_idx = pp.create_gen(
            self.net,
            bus=bus_idx,
            p_mw=spec.p_mw,
            q_mvar=spec.q_mvar if spec.q_mvar is not None else 0.0,
            slack=spec.slack,
            name=spec.name,
            min_p_mw=spec.p_min_mw,
            max_p_mw=spec.p_max_mw if spec.p_max_mw is not None else spec.p_mw * 2
        )
        self.gen_specs[spec.name] = spec
        return gen_idx
    
    # Add a static load to the network, returns load index
    def add_load(self, spec: LoadSpec) -> int:
        bus_idx = self.net.bus[self.net.bus['name'] == spec.bus].index[0]
        
        load_idx = pp.create_load(
            self.net,
            bus=bus_idx,
            p_mw=spec.p_mw,
            q_mvar=spec.q_mvar,
            name=spec.name
        )
        self.load_specs[spec.name] = spec
        return load_idx
    
    
    # Add a flexible load (data center) to the network, returns FlexibleLoad object
    def add_flexible_load(self, spec: FlexibleLoadSpec) -> 'FlexibleLoad':
        bus_idx = self.net.bus[self.net.bus['name'] == spec.bus].index[0]
        
        # Create as a regular load, but wrap in FlexibleLoad for tracking
        load_idx = pp.create_load(
            self.net,
            bus=bus_idx,
            p_mw=spec.baseline_mw,  # Start at baseline
            q_mvar=0.0,
            name=spec.name
        )
        
        flex_load = FlexibleLoad(
            name=spec.name,
            load_idx=load_idx,
            baseline_mw=spec.baseline_mw,
            min_mw=spec.min_mw,
            max_mw=spec.max_mw,
            deferrable_pct=spec.deferrable_pct,
            defer_window_hours=spec.defer_window_hours,
            network=self
        )
        self.flex_load_specs[spec.name] = spec
        return flex_load
    

    # Add a static generator (renewable DER) to the network, returns sgen index
    def add_static_gen(self, spec: StaticGeneratorSpec) -> int:
        bus_idx = self.net.bus[self.net.bus['name'] == spec.bus].index[0]
        
        sgen_idx = pp.create_sgen(
            self.net,
            bus=bus_idx,
            p_mw=spec.p_mw,
            q_mvar=spec.q_mvar,
            name=spec.name
        )
        self.sgen_specs[spec.name] = spec
        return sgen_idx
    
    # Run AC power flow on the network
    def run_power_flow(self, check_convergence: bool = True) -> bool:
        try:
            pp.runpp(self.net, check_convergence=check_convergence)
            return True
        except pp.LoadflowNotConverged:
            print(f"[WARNING] Power flow did not converge for {self.name}")
            return False
    
    # Get voltage magnitude (p.u.) and angle (degrees) for a bus
    def get_bus_voltage(self, bus_name: str) -> Tuple[float, float]:
        bus_idx = self.net.bus[self.net.bus['name'] == bus_name].index[0]
        vm_pu = self.net.res_bus.loc[bus_idx, 'vm_pu']
        va_degree = self.net.res_bus.loc[bus_idx, 'va_degree']
        return vm_pu, va_degree
    
    # Get loading percentage (0-100%) for a line
    def get_line_loading(self, line_name: str) -> float:
        line_idx = self.net.line[self.net.line['name'] == line_name].index[0]
        loading_pct = self.net.res_line.loc[line_idx, 'loading_percent']
        return loading_pct
    
    # Get real and reactive power output for a generator
    def get_generator_output(self, gen_name: str) -> Tuple[float, float]:
        gen_idx = self.net.gen[self.net.gen['name'] == gen_name].index[0]
        p_mw = self.net.res_gen.loc[gen_idx, 'p_mw']
        q_mvar = self.net.res_gen.loc[gen_idx, 'q_mvar']
        return p_mw, q_mvar
    
    # Get real and reactive power consumption for a load
    def get_load_consumption(self, load_name: str) -> Tuple[float, float]:
        load_idx = self.net.load[self.net.load['name'] == load_name].index[0]
        p_mw = self.net.res_load.loc[load_idx, 'p_mw']
        q_mvar = self.net.res_load.loc[load_idx, 'q_mvar']
        return p_mw, q_mvar
    
    # Check grid state against all constraint thresholds, returns {constraint: is_violated}
    def check_constraints(self) -> Dict[str, bool]:
        violations = {}
        
        # Check line loading
        max_loading = self.net.res_line['loading_percent'].max()
        violations['line_loading'] = max_loading > self.constraints['line_loading_max_pct']
        
        # Check voltage bounds
        min_voltage = self.net.res_bus['vm_pu'].min()
        max_voltage = self.net.res_bus['vm_pu'].max()
        violations['voltage_min'] = min_voltage < self.constraints['voltage_min_pu']
        violations['voltage_max'] = max_voltage > self.constraints['voltage_max_pu']
        
        # Check reserve margin: generation + renewables minus demand
        total_gen  = self.net.res_gen['p_mw'].sum()
        total_sgen = self.net.res_sgen['p_mw'].sum() if not self.net.sgen.empty else 0.0
        total_load = self.net.res_load['p_mw'].sum()
        reserve = total_gen + total_sgen - total_load
        violations['reserve_margin'] = reserve < self.constraints['reserve_margin_mw']
        
        return violations
    
    # Get a summary of current grid state
    def get_state_summary(self) -> Dict:
        online = self.net.gen["in_service"] == True
        reserve_mw = (
            self.net.gen.loc[online, "max_p_mw"].sum()
            - self.net.res_gen.loc[online, "p_mw"].sum()
        ) if not self.net.gen.empty else 0.0
        return {
            'name': self.name,
            'n_buses': len(self.net.bus),
            'n_lines': len(self.net.line),
            'n_generators': len(self.net.gen),
            'n_loads': len(self.net.load),
            'total_gen_mw': self.net.res_gen['p_mw'].sum(),
            'total_load_mw': self.net.res_load['p_mw'].sum(),
            'total_sgen_mw': self.net.res_sgen['p_mw'].sum(),
            'reserve_margin_mw': reserve_mw,
            'max_line_loading_pct': self.net.res_line['loading_percent'].max(),
            'min_bus_voltage_pu': self.net.res_bus['vm_pu'].min(),
            'max_bus_voltage_pu': self.net.res_bus['vm_pu'].max(),
            'converged': self.net.converged,
        }
 
 
# Flexible load (data center, EV charging, etc.) — can shift, defer, or curtail demand
class FlexibleLoad:
    
    def __init__(
        self,
        name: str,
        load_idx: int,
        baseline_mw: float,
        min_mw: float,
        max_mw: float,
        deferrable_pct: float,
        defer_window_hours: int,
        network: GridNetwork
    ):
        self.name = name
        self.load_idx = load_idx
        self.baseline_mw = baseline_mw
        self.min_mw = min_mw
        self.max_mw = max_mw
        self.deferrable_pct = deferrable_pct
        self.defer_window_hours = defer_window_hours
        self.network = network
        
        # Current state
        self.current_mw = baseline_mw
        self.deferred_mw = 0.0  # Amount currently deferred
        
    # Set load to a new value within [min_mw, max_mw], returns False if out of bounds
    def set_load(self, p_mw: float) -> bool:
        if p_mw < self.min_mw or p_mw > self.max_mw:
            print(f"[ERROR] Load {self.name}: {p_mw} MW outside bounds [{self.min_mw}, {self.max_mw}]")
            return False
        
        self.current_mw = p_mw
        self.network.net.load.loc[self.load_idx, 'p_mw'] = p_mw
        return True
    
    # Defer a portion of load to a future time window, returns False if over the deferrable limit
    def defer_load(self, defer_mw: float) -> bool:
        max_deferrable = self.baseline_mw * self.deferrable_pct
        if defer_mw > max_deferrable:
            print(f"[ERROR] Load {self.name}: Cannot defer {defer_mw} MW (max {max_deferrable} MW)")
            return False
        
        self.deferred_mw = defer_mw
        new_load = self.baseline_mw - defer_mw
        return self.set_load(new_load)
    
    # Curtail load by curtail_pct (0.0-1.0) of baseline, returns False if result is out of bounds
    def curtail_load(self, curtail_pct: float) -> bool:
        reduction = self.baseline_mw * curtail_pct
        new_load = max(self.baseline_mw - reduction, self.min_mw)
        return self.set_load(new_load)
    
    # Return load to baseline and clear any deferred amount
    def restore_baseline(self):
        self.deferred_mw = 0.0
        self.set_load(self.baseline_mw)
    
    # Get current flexible load state
    def get_state(self) -> Dict:
        return {
            'name': self.name,
            'current_mw': self.current_mw,
            'baseline_mw': self.baseline_mw,
            'deferred_mw': self.deferred_mw,
            'min_mw': self.min_mw,
            'max_mw': self.max_mw,
            'deferrable_pct': self.deferrable_pct,
        }
 
 
if __name__ == "__main__":
    # Example: Create a minimal 3-bus test case
    print("[BOILERPLATE] Grid Network Module Loaded")
    print("Define your grid by creating BusSpec, LineSpec, GeneratorSpec objects.")
    print("Then use GridNetwork to assemble them into a pandapower network.")