"""
Grid Event System
=================
Defines, schedules, and injects discrete events (faults, surges, outages)
into a live GridNetwork. Keeps event logic separate from topology.

Usage:
    from simulator.events import EventScheduler, GridEvent, EventType
    scheduler = EventScheduler(grid)
    scheduler.schedule(GridEvent("surge_1", EventType.POWER_SURGE, target="Bus_A",
                                 scheduled_at=10.0, duration_steps=3,
                                 params={"magnitude_mw": 50.0}))
    scheduler.tick(timestep=10.0)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional, Any

if TYPE_CHECKING:
    from simulator.network import GridNetwork


class EventType(str, Enum):
    POWER_SURGE          = "power_surge"          # Sudden load spike at a bus
    WEATHER_OUTAGE       = "weather_outage"        # Generator/line derated by weather
    LINE_TRIP            = "line_trip"             # Transmission line disconnected
    GENERATOR_TRIP       = "generator_trip"        # Generator forced offline
    LOAD_SPIKE           = "load_spike"            # Demand increase at a load
    # ── AI data center events ─────────────────────────────────────────────────
    AI_TRAINING_SPIKE    = "ai_training_spike"     # Training job starts: random-magnitude step up
    AI_TRAINING_DROPOUT  = "ai_training_dropout"   # Job ends/crashes: sudden load drop
    COOLING_CASCADE      = "cooling_cascade"       # Compute spike + delayed cooling load
    LOAD_OSCILLATION     = "load_oscillation"      # Power-electronics hunting: sinusoidal swing


@dataclass
class GridEvent:
    name: str
    event_type: EventType
    target: str           # Name of the bus / line / gen / load to affect
    scheduled_at: float   # Simulation timestep at which to apply the event
    duration_steps: int   # How many ticks the event lasts (1 = instantaneous)
    params: Dict[str, Any] = field(default_factory=dict)

    # Internal tracking — not set by caller
    _applied_at: Optional[float] = field(default=None, repr=False, compare=False)
    _snapshot: Dict[str, Any]    = field(default_factory=dict, repr=False, compare=False)


class EventScheduler:
    """
    Manages the lifecycle of GridEvents against a live GridNetwork.

    Call tick(timestep) each simulation step. It will:
      1. Apply any events whose scheduled_at == timestep.
      2. Expire (revert) any events that have exhausted their duration.
    """

    def __init__(self, grid: GridNetwork):
        self.grid = grid
        self._pending:  List[GridEvent] = []   # Not yet applied
        self._active:   List[GridEvent] = []   # Currently applied
        self._history:  List[GridEvent] = []   # Completed/expired

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def schedule(self, event: GridEvent) -> None:
        """Queue a deterministic event to fire at event.scheduled_at."""
        self._pending.append(event)
        self._pending.sort(key=lambda e: e.scheduled_at)

    def inject_random(
        self,
        event_type: EventType,
        target: str,
        probability: float,
        current_timestep: float,
        duration_steps: int = 1,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[GridEvent]:
        """
        Roll against probability and schedule an event starting NOW if it fires.

        Args:
            probability: 0.0–1.0 chance of the event occurring this tick.

        Returns:
            The created GridEvent if it fired, else None.
        """
        if random.random() > probability:
            return None

        event = GridEvent(
            name=f"{event_type.value}_{target}_{int(current_timestep)}",
            event_type=event_type,
            target=target,
            scheduled_at=current_timestep,
            duration_steps=duration_steps,
            params=params or {},
        )
        self.schedule(event)
        return event

    def tick(self, timestep: float) -> Dict[str, List[GridEvent]]:
        """
        Advance the scheduler by one simulation step.

        Returns a dict with keys "applied" and "expired" listing events
        that changed state this tick — useful for agent observation.
        """
        applied = self._apply_due(timestep)
        self._update_active(timestep)   # per-tick mutation (e.g. oscillation, cooling cascade)
        expired = self._expire_finished(timestep)
        return {"applied": applied, "expired": expired}

    def active_events(self) -> List[GridEvent]:
        return list(self._active)

    def pending_events(self) -> List[GridEvent]:
        return list(self._pending)

    def event_history(self) -> List[GridEvent]:
        return list(self._history)

    def force_resolve(self, event_name: str) -> bool:
        """Operator-approved early resolution: revert and retire a named active event."""
        for event in self._active:
            if event.name == event_name:
                self._revert(event)
                self._active.remove(event)
                self._history.append(event)
                print(f"[EVENT] Resolved | {event.event_type.value:20s} | target={event.target} | operator-approved")
                return True
        return False

    # ------------------------------------------------------------------
    # Internal — apply / expire
    # ------------------------------------------------------------------

    def _update_active(self, timestep: float) -> None:
        update_handlers = {
            EventType.LOAD_OSCILLATION: self._update_load_oscillation,
            EventType.COOLING_CASCADE:  self._update_cooling_cascade,
        }
        for event in self._active:
            handler = update_handlers.get(event.event_type)
            if handler:
                try:
                    handler(event, timestep)
                except Exception as exc:
                    print(f"[EVENT] Failed to update {event.name}: {exc}")

    def _apply_due(self, timestep: float) -> List[GridEvent]:
        due, remaining = [], []
        for event in self._pending:
            if event.scheduled_at <= timestep:
                due.append(event)
            else:
                remaining.append(event)
        self._pending = remaining

        applied = []
        for event in due:
            success = self._apply(event, timestep)
            if success:
                event._applied_at = timestep
                self._active.append(event)
                applied.append(event)
                print(f"[EVENT] Applied  | {event.event_type.value:20s} | target={event.target} | t={timestep}")
        return applied

    def _expire_finished(self, timestep: float) -> List[GridEvent]:
        still_active, expired = [], []
        for event in self._active:
            steps_elapsed = timestep - event._applied_at
            if steps_elapsed >= event.duration_steps:
                self._revert(event)
                self._history.append(event)
                expired.append(event)
                print(f"[EVENT] Expired  | {event.event_type.value:20s} | target={event.target} | t={timestep}")
            else:
                still_active.append(event)
        self._active = still_active
        return expired

    # ------------------------------------------------------------------
    # Apply handlers — save snapshot, then mutate grid
    # ------------------------------------------------------------------

    def _apply(self, event: GridEvent, timestep: float) -> bool:
        handlers = {
            EventType.POWER_SURGE:         self._apply_power_surge,
            EventType.WEATHER_OUTAGE:      self._apply_weather_outage,
            EventType.LINE_TRIP:           self._apply_line_trip,
            EventType.GENERATOR_TRIP:      self._apply_generator_trip,
            EventType.LOAD_SPIKE:          self._apply_load_spike,
            EventType.AI_TRAINING_SPIKE:   self._apply_ai_training_spike,
            EventType.AI_TRAINING_DROPOUT: self._apply_ai_training_dropout,
            EventType.COOLING_CASCADE:     self._apply_cooling_cascade,
            EventType.LOAD_OSCILLATION:    self._apply_load_oscillation,
        }
        handler = handlers.get(event.event_type)
        if handler is None:
            print(f"[EVENT] Unknown event type: {event.event_type}")
            return False
        try:
            handler(event)
            return True
        except Exception as exc:
            print(f"[EVENT] Failed to apply {event.name}: {exc}")
            return False

    def _apply_power_surge(self, event: GridEvent) -> None:
        """Spike load at every load connected to the target bus."""
        net = self.grid.net
        bus_idx = net.bus[net.bus["name"] == event.target].index[0]
        load_mask = net.load["bus"] == bus_idx
        event._snapshot["loads"] = net.load.loc[load_mask, "p_mw"].to_dict()

        magnitude_mw = event.params.get("magnitude_mw", 50.0)
        net.load.loc[load_mask, "p_mw"] += magnitude_mw

    def _apply_weather_outage(self, event: GridEvent) -> None:
        """Derate a generator's output due to weather (wind/solar drop, ice, etc.)."""
        net = self.grid.net
        derate_pct = event.params.get("derate_pct", 0.5)  # 0.0 = full offline, 1.0 = no change

        gen_mask = net.gen["name"] == event.target
        if gen_mask.any():
            event._snapshot["gen_p_mw"] = net.gen.loc[gen_mask, "p_mw"].to_dict()
            net.gen.loc[gen_mask, "p_mw"] *= (1.0 - derate_pct)
            return

        sgen_mask = net.sgen["name"] == event.target
        if sgen_mask.any():
            event._snapshot["sgen_p_mw"] = net.sgen.loc[sgen_mask, "p_mw"].to_dict()
            net.sgen.loc[sgen_mask, "p_mw"] *= (1.0 - derate_pct)
            return

        raise ValueError(f"Generator '{event.target}' not found in gen or sgen tables.")

    def _apply_line_trip(self, event: GridEvent) -> None:
        """Take a transmission line out of service."""
        net = self.grid.net
        line_mask = net.line["name"] == event.target
        if not line_mask.any():
            raise ValueError(f"Line '{event.target}' not found.")
        event._snapshot["in_service"] = net.line.loc[line_mask, "in_service"].to_dict()
        net.line.loc[line_mask, "in_service"] = False

    def _apply_generator_trip(self, event: GridEvent) -> None:
        """Force a generator offline instantly."""
        net = self.grid.net
        gen_mask = net.gen["name"] == event.target
        if not gen_mask.any():
            raise ValueError(f"Generator '{event.target}' not found.")
        event._snapshot["in_service"] = net.gen.loc[gen_mask, "in_service"].to_dict()
        net.gen.loc[gen_mask, "in_service"] = False

    def _apply_load_spike(self, event: GridEvent) -> None:
        """Increase a named load's demand by a fixed or percentage amount."""
        net = self.grid.net
        load_mask = net.load["name"] == event.target
        if not load_mask.any():
            raise ValueError(f"Load '{event.target}' not found.")

        event._snapshot["p_mw"] = net.load.loc[load_mask, "p_mw"].to_dict()

        if "delta_mw" in event.params:
            net.load.loc[load_mask, "p_mw"] += event.params["delta_mw"]
        elif "scale_factor" in event.params:
            net.load.loc[load_mask, "p_mw"] *= event.params["scale_factor"]

    # ------------------------------------------------------------------
    # Apply handlers — AI data center events
    # ------------------------------------------------------------------

    # Training job starts: magnitude drawn randomly so the grid operator has no
    # advance notice of how large the step will be.
    def _apply_ai_training_spike(self, event: GridEvent) -> None:
        net = self.grid.net
        load_mask = net.load["name"] == event.target
        if not load_mask.any():
            raise ValueError(f"Load '{event.target}' not found.")
        min_mw = event.params.get("min_mw", 30.0)
        max_mw = event.params.get("max_mw", 120.0)
        magnitude_mw = random.uniform(min_mw, max_mw)
        event._snapshot["p_mw"] = net.load.loc[load_mask, "p_mw"].to_dict()
        event._snapshot["magnitude_mw"] = magnitude_mw
        net.load.loc[load_mask, "p_mw"] += magnitude_mw
        print(f"[EVENT] AI training spike: '{event.target}' +{magnitude_mw:.1f} MW "
              f"(sampled from [{min_mw}, {max_mw}] MW)")

    # Training job ends or crashes: load drops by dropout_pct of current draw.
    # A sudden large drop can cause over-voltage and frequency excursion.
    def _apply_ai_training_dropout(self, event: GridEvent) -> None:
        net = self.grid.net
        load_mask = net.load["name"] == event.target
        if not load_mask.any():
            raise ValueError(f"Load '{event.target}' not found.")
        dropout_pct = event.params.get("dropout_pct", 0.80)
        current_mw = float(net.load.loc[load_mask, "p_mw"].values[0])
        drop_mw = current_mw * dropout_pct
        event._snapshot["p_mw"] = net.load.loc[load_mask, "p_mw"].to_dict()
        net.load.loc[load_mask, "p_mw"] -= drop_mw
        print(f"[EVENT] AI training dropout: '{event.target}' -{drop_mw:.1f} MW "
              f"({dropout_pct*100:.0f}% of {current_mw:.1f} MW dropped)")

    # Phase 1 (immediate): compute load spikes. Phase 2: cooling load adds on
    # after cooling_delay ticks, emulating thermal lag in the data center.
    def _apply_cooling_cascade(self, event: GridEvent) -> None:
        net = self.grid.net
        load_mask = net.load["name"] == event.target
        if not load_mask.any():
            raise ValueError(f"Load '{event.target}' not found.")
        compute_mw = event.params.get("compute_mw", 50.0)
        event._snapshot["p_mw"] = net.load.loc[load_mask, "p_mw"].to_dict()
        event._snapshot["cooling_applied"] = False
        net.load.loc[load_mask, "p_mw"] += compute_mw
        print(f"[EVENT] Cooling cascade phase 1 (compute): '{event.target}' +{compute_mw:.1f} MW")

    def _update_cooling_cascade(self, event: GridEvent, timestep: float) -> None:
        if event._snapshot.get("cooling_applied"):
            return
        steps_elapsed = timestep - event._applied_at
        cooling_delay = event.params.get("cooling_delay", 3)
        if steps_elapsed >= cooling_delay:
            net = self.grid.net
            load_mask = net.load["name"] == event.target
            cooling_mw = event.params.get("cooling_mw", 20.0)
            net.load.loc[load_mask, "p_mw"] += cooling_mw
            event._snapshot["cooling_applied"] = True
            print(f"[EVENT] Cooling cascade phase 2 (thermal): '{event.target}' +{cooling_mw:.1f} MW")

    # Power-electronics loads interact with grid voltage controls, causing the
    # draw to oscillate sinusoidally rather than hold steady.
    def _apply_load_oscillation(self, event: GridEvent) -> None:
        net = self.grid.net
        load_mask = net.load["name"] == event.target
        if not load_mask.any():
            raise ValueError(f"Load '{event.target}' not found.")
        event._snapshot["p_mw"] = net.load.loc[load_mask, "p_mw"].to_dict()
        event._snapshot["p_mw_baseline"] = float(net.load.loc[load_mask, "p_mw"].values[0])
        amplitude = event.params.get("amplitude_mw", 10.0)
        period    = event.params.get("period_steps", 4.0)
        print(f"[EVENT] Load oscillation started: '{event.target}' "
              f"±{amplitude} MW, period={period} ticks")

    def _update_load_oscillation(self, event: GridEvent, timestep: float) -> None:
        net = self.grid.net
        load_mask = net.load["name"] == event.target
        baseline  = event._snapshot["p_mw_baseline"]
        amplitude = event.params.get("amplitude_mw", 10.0)
        period    = event.params.get("period_steps", 4.0)
        steps_elapsed = timestep - event._applied_at
        offset = amplitude * math.sin(2 * math.pi * steps_elapsed / period)
        net.load.loc[load_mask, "p_mw"] = baseline + offset

    # ------------------------------------------------------------------
    # Revert handlers — restore saved snapshot
    # ------------------------------------------------------------------

    def _revert(self, event: GridEvent) -> None:
        try:
            if event.event_type == EventType.POWER_SURGE:
                self._revert_power_surge(event)
            elif event.event_type == EventType.WEATHER_OUTAGE:
                self._revert_weather_outage(event)
            elif event.event_type == EventType.LINE_TRIP:
                self._revert_line_trip(event)
            elif event.event_type == EventType.GENERATOR_TRIP:
                self._revert_generator_trip(event)
            elif event.event_type == EventType.LOAD_SPIKE:
                self._revert_load_spike(event)
            elif event.event_type in (
                EventType.AI_TRAINING_SPIKE,
                EventType.AI_TRAINING_DROPOUT,
                EventType.COOLING_CASCADE,
                EventType.LOAD_OSCILLATION,
            ):
                self._revert_load_snapshot(event)
        except Exception as exc:
            print(f"[EVENT] Failed to revert {event.name}: {exc}")

    def _revert_power_surge(self, event: GridEvent) -> None:
        for idx, val in event._snapshot.get("loads", {}).items():
            self.grid.net.load.loc[idx, "p_mw"] = val

    def _revert_weather_outage(self, event: GridEvent) -> None:
        if "gen_p_mw" in event._snapshot:
            for idx, val in event._snapshot["gen_p_mw"].items():
                self.grid.net.gen.loc[idx, "p_mw"] = val
        if "sgen_p_mw" in event._snapshot:
            for idx, val in event._snapshot["sgen_p_mw"].items():
                self.grid.net.sgen.loc[idx, "p_mw"] = val

    def _revert_line_trip(self, event: GridEvent) -> None:
        for idx, val in event._snapshot.get("in_service", {}).items():
            self.grid.net.line.loc[idx, "in_service"] = val

    def _revert_generator_trip(self, event: GridEvent) -> None:
        for idx, val in event._snapshot.get("in_service", {}).items():
            self.grid.net.gen.loc[idx, "in_service"] = val

    def _revert_load_spike(self, event: GridEvent) -> None:
        for idx, val in event._snapshot.get("p_mw", {}).items():
            self.grid.net.load.loc[idx, "p_mw"] = val

    # Shared revert for all DC events — restores p_mw from snapshot
    def _revert_load_snapshot(self, event: GridEvent) -> None:
        for idx, val in event._snapshot.get("p_mw", {}).items():
            self.grid.net.load.loc[idx, "p_mw"] = val
