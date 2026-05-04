"""
Power Flow Solver
==================
Wrapper around pandapower's AC power flow engine.
Handles convergence, constraint checking, and state validation.

Usage:
    from power_flow import PowerFlowEngine
    engine = PowerFlowEngine(grid)
    status = engine.run()
    violations = engine.check_constraints()
    report = engine.generate_report()
"""

import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import pandapower as pp


@dataclass
class ConstraintViolation:
    """A single constraint violation."""
    violation_type: str  # "line_loading", "voltage", "frequency", "reserve"
    component_name: str
    current_value: float
    limit_value: float
    severity: str  # "warning" or "critical"


class PowerFlowEngine:
    """
    Manages power flow computation and constraint validation.
    """
    
    def __init__(self, grid_network):
        """
        Initialize the power flow engine.
        
        Args:
            grid_network: GridNetwork instance with populated network.
        """
        self.grid = grid_network
        self.net = grid_network.net
        self.converged = False
        self.violations: List[ConstraintViolation] = []
        self.last_report: Optional[Dict] = None
        
    def run(self, verbose: bool = False) -> bool:
        """
        Run AC power flow on the network.
        
        Args:
            verbose: Print convergence details
        
        Returns:
            True if converged, False otherwise.
        """
        try:
            pp.runpp(self.net, check_convergence=True, numba=False)
            self.converged = True
            if verbose:
                print("[PF] Power flow converged successfully")
            return True
        except pp.LoadflowNotConverged as e:
            self.converged = False
            if verbose:
                print(f"[PF] Power flow FAILED to converge: {e}")
            return False
    
    def check_constraints(self) -> Dict[str, bool]:
        """
        Check grid state against all constraint thresholds.
        
        Returns:
            Dictionary mapping constraint name to violation status (True = violated).
        """
        if not self.converged:
            return {'convergence': True}  # True = violated
        
        violations_dict = {}
        self.violations = []
        
        # --- Check line thermal loading ---
        line_loading = self.net.res_line['loading_percent']
        max_loading = line_loading.max()
        limit = self.grid.constraints['line_loading_max_pct']
        
        violations_dict['line_loading'] = max_loading > limit
        
        # Record per-line violations
        overloaded = self.net.res_line[line_loading > limit]
        for idx, row in overloaded.iterrows():
            line_name = self.net.line.loc[idx, 'name']
            self.violations.append(ConstraintViolation(
                violation_type='line_loading',
                component_name=line_name,
                current_value=row['loading_percent'],
                limit_value=limit,
                severity='critical' if row['loading_percent'] > 100 else 'warning'
            ))
        
        # --- Check voltage bounds ---
        bus_voltages = self.net.res_bus['vm_pu']
        v_min = bus_voltages.min()
        v_max = bus_voltages.max()
        
        violations_dict['voltage_min'] = v_min < self.grid.constraints['voltage_min_pu']
        violations_dict['voltage_max'] = v_max > self.grid.constraints['voltage_max_pu']
        
        # Record per-bus voltage violations
        low_voltage = self.net.res_bus[bus_voltages < self.grid.constraints['voltage_min_pu']]
        for idx, row in low_voltage.iterrows():
            bus_name = self.net.bus.loc[idx, 'name']
            self.violations.append(ConstraintViolation(
                violation_type='voltage',
                component_name=bus_name,
                current_value=row['vm_pu'],
                limit_value=self.grid.constraints['voltage_min_pu'],
                severity='critical'
            ))
        
        high_voltage = self.net.res_bus[bus_voltages > self.grid.constraints['voltage_max_pu']]
        for idx, row in high_voltage.iterrows():
            bus_name = self.net.bus.loc[idx, 'name']
            self.violations.append(ConstraintViolation(
                violation_type='voltage',
                component_name=bus_name,
                current_value=row['vm_pu'],
                limit_value=self.grid.constraints['voltage_max_pu'],
                severity='warning'
            ))
        
        # --- Check reserve margin (spinning reserve) ---
        # Headroom = Σ(max_p_mw − actual_p_mw) across online generators.
        # gen+sgen−load ≈ losses only (slack balances exactly), so that formula
        # produces ~4 MW regardless of grid stress — use capacity headroom instead.
        if not self.net.gen.empty:
            online = self.net.gen["in_service"] == True
            reserve = (
                self.net.gen.loc[online, "max_p_mw"].sum()
                - self.net.res_gen.loc[online, "p_mw"].sum()
            )
        else:
            reserve = 0.0
        min_reserve = self.grid.constraints['reserve_margin_mw']
        
        violations_dict['reserve_margin'] = reserve < min_reserve
        
        if reserve < min_reserve:
            self.violations.append(ConstraintViolation(
                violation_type='reserve',
                component_name='system',
                current_value=reserve,
                limit_value=min_reserve,
                severity='critical'
            ))
        
        return violations_dict
    
    def get_bus_state(self, bus_name: str) -> Dict:
        """Get detailed state for a single bus."""
        try:
            bus_idx = self.net.bus[self.net.bus['name'] == bus_name].index[0]
        except IndexError:
            raise ValueError(f"Bus '{bus_name}' not found")
        
        bus_result = self.net.res_bus.loc[bus_idx]
        
        return {
            'name': bus_name,
            'vn_kv': self.net.bus.loc[bus_idx, 'vn_kv'],
            'vm_pu': bus_result['vm_pu'],
            'va_degree': bus_result['va_degree'],
            'p_mw': bus_result['p_mw'],
            'q_mvar': bus_result['q_mvar'],
        }
    
    def get_line_state(self, line_name: str) -> Dict:
        """Get detailed state for a single line."""
        try:
            line_idx = self.net.line[self.net.line['name'] == line_name].index[0]
        except IndexError:
            raise ValueError(f"Line '{line_name}' not found")
        
        line_result = self.net.res_line.loc[line_idx]
        
        from_bus = self.net.line.loc[line_idx, 'from_bus']
        to_bus = self.net.line.loc[line_idx, 'to_bus']
        from_name = self.net.bus.loc[from_bus, 'name']
        to_name = self.net.bus.loc[to_bus, 'name']
        
        s_from_mva = math.sqrt(line_result['p_from_mw']**2 + line_result['q_from_mvar']**2) / 1000
        s_to_mva   = math.sqrt(line_result['p_to_mw']**2   + line_result['q_to_mvar']**2)   / 1000
        return {
            'name': line_name,
            'from_bus': from_name,
            'to_bus': to_name,
            's_from_mva': s_from_mva,
            's_to_mva': s_to_mva,
            'i_from_ka': line_result['i_from_ka'],
            'i_to_ka': line_result['i_to_ka'],
            'loading_percent': line_result['loading_percent'],
        }
    
    def get_generator_state(self, gen_name: str) -> Dict:
        """Get detailed state for a single generator."""
        try:
            gen_idx = self.net.gen[self.net.gen['name'] == gen_name].index[0]
        except IndexError:
            raise ValueError(f"Generator '{gen_name}' not found")
        
        gen_result = self.net.res_gen.loc[gen_idx]
        
        bus = self.net.gen.loc[gen_idx, 'bus']
        bus_name = self.net.bus.loc[bus, 'name']
        
        return {
            'name': gen_name,
            'bus': bus_name,
            'p_mw': gen_result['p_mw'],
            'q_mvar': gen_result['q_mvar'],
            'va_degree': self.net.res_bus.loc[bus, 'va_degree'],
        }
    
    def get_load_state(self, load_name: str) -> Dict:
        """Get detailed state for a single load."""
        try:
            load_idx = self.net.load[self.net.load['name'] == load_name].index[0]
        except IndexError:
            raise ValueError(f"Load '{load_name}' not found")
        
        load_result = self.net.res_load.loc[load_idx]
        
        bus = self.net.load.loc[load_idx, 'bus']
        bus_name = self.net.bus.loc[bus, 'name']
        
        return {
            'name': load_name,
            'bus': bus_name,
            'p_mw': load_result['p_mw'],
            'q_mvar': load_result['q_mvar'],
        }
    
    def generate_report(self) -> Dict:
        """
        Generate a comprehensive report of grid state and violations.
        
        Returns:
            Dictionary with summary, component states, and violations.
        """
        if not self.converged:
            return {'status': 'failed', 'reason': 'Power flow did not converge'}
        
        # Summary
        total_gen = self.net.res_gen['p_mw'].sum() if not self.net.gen.empty else 0.0
        total_load = self.net.res_load['p_mw'].sum() if not self.net.load.empty else 0.0
        total_sgen = self.net.res_sgen['p_mw'].sum() if not self.net.sgen.empty else 0.0
        if not self.net.gen.empty:
            online = self.net.gen["in_service"] == True
            reserve = (
                self.net.gen.loc[online, "max_p_mw"].sum()
                - self.net.res_gen.loc[online, "p_mw"].sum()
            )
        else:
            reserve = 0.0
        
        summary = {
            'status': 'converged',
            'total_generation_mw': total_gen,
            'total_load_mw': total_load,
            'total_renewable_mw': total_sgen,
            'reserve_margin_mw': reserve,
            'max_line_loading_pct': self.net.res_line['loading_percent'].max() if not self.net.line.empty else 0.0,
            'min_bus_voltage_pu': self.net.res_bus['vm_pu'].min(),
            'max_bus_voltage_pu': self.net.res_bus['vm_pu'].max(),
        }
        
        # Component states
        generators = {}
        for idx, row in self.net.gen.iterrows():
            gen_name = row['name']
            generators[gen_name] = self.get_generator_state(gen_name)
        
        loads = {}
        for idx, row in self.net.load.iterrows():
            load_name = row['name']
            loads[load_name] = self.get_load_state(load_name)
        
        lines = {}
        for idx, row in self.net.line.iterrows():
            line_name = row['name']
            lines[line_name] = self.get_line_state(line_name)
        
        # Violations
        violations_summary = []
        for violation in self.violations:
            violations_summary.append({
                'type': violation.violation_type,
                'component': violation.component_name,
                'current': violation.current_value,
                'limit': violation.limit_value,
                'severity': violation.severity,
            })
        
        report = {
            'summary': summary,
            'generators': generators,
            'loads': loads,
            'lines': lines,
            'violations': violations_summary,
            'n_violations': len(self.violations),
        }
        
        self.last_report = report
        return report
    
    def print_report(self):
        """Pretty-print the current report."""
        if self.last_report is None:
            print("[PF] No report generated yet. Call generate_report() first.")
            return
        
        report = self.last_report
        print("\n" + "="*70)
        print(f"POWER FLOW REPORT")
        print("="*70)
        
        summary = report['summary']
        print(f"\nStatus: {summary['status']}")
        print(f"Generation: {summary['total_generation_mw']:.2f} MW")
        print(f"Load: {summary['total_load_mw']:.2f} MW")
        print(f"Renewable: {summary['total_renewable_mw']:.2f} MW")
        print(f"Reserve: {summary['reserve_margin_mw']:.2f} MW")
        print(f"Max line loading: {summary['max_line_loading_pct']:.1f}%")
        print(f"Voltage range: {summary['min_bus_voltage_pu']:.3f} - {summary['max_bus_voltage_pu']:.3f} p.u.")
        
        if report['n_violations'] > 0:
            print(f"\n⚠️  VIOLATIONS: {report['n_violations']}")
            for v in report['violations']:
                print(f"  - {v['type']}: {v['component']} = {v['current']:.2f} (limit {v['limit']:.2f}) [{v['severity']}]")
        else:
            print(f"\n✓ No violations")
        
        print("\n" + "="*70)


if __name__ == "__main__":
    print("[BOILERPLATE] Power Flow Engine Module Loaded")
    print("Use with a GridNetwork instance to run and validate power flow.")