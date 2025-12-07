"""
MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

"""
Zone Wrapper: Individual Zone PID Controller with Priority & TRV Mitigation

Each zone (room) has a ZoneWrapper that:
1. Reads current/target temperature from Home Assistant climate entity
2. Calculates temperature error (target - current)
3. Implements PID algorithm to calculate zone's heating demand
4. Handles TRV valve opening percentage to mitigate valve effects
5. Supports zone priority weighting for boiler cycling prevention
6. Resets PID on target temperature changes
7. Exports PID settings for Home Assistant monitoring

PID Controller Components:
- P (Proportional): Immediate response proportional to error
  P = error * KP (0.5)
  
- I (Integral): Long-term correction for persistent error
  I_sum accumulates error * time_delta
  I = I_sum * KI (0.01)
  
- D (Derivative): Damping based on error rate of change
  D = (error_now - error_prev) / time_delta * KD (0.1)

TRV Mitigation:
- TRV valves close in 25% steps as they approach setpoint
- Closing reduces flow: opening_percent determines proportional boost to error
- Example: 50% opening (valve closing) -> error *= 2.0 to compensate
  
Priority System:
- priority=1.0: Normal importance, full demand counted
- priority=0.5: Half importance, lower demand threshold for boiler activation
- priority=0.1: Low importance, aggregated demands to prevent cycling

Output: PID = P + I + D (mapped to physical flow temperature by MasterController)
"""

import time
import logging
from typing import Optional

# =========================================================
# PID Tuning Constants
# =========================================================

# Proportional gain: How strongly to respond to current error
# Higher KP = faster response but may overshoot
KP = 0.5

# Integral gain: How strongly to correct persistent error
# Higher KI = better steady-state accuracy but may cause oscillation
KI = 0.01

# Derivative gain: How much to dampen rapid error changes
# Higher KD = smoother response but may reduce reactivity
KD = 0.1

_LOGGER = logging.getLogger("don_controller")


class ZoneWrapper:
    """
    Wrapper for a Home Assistant thermostat entity with embedded PID controller.
    
    Manages:
    - Zone metadata (name, entity_id, floor area, priority)
    - Temperature readings and error calculation
    - PID state (integral sum, last error, update time)
    - Heating demand determination (is zone actively heating?)
    - TRV valve opening percentage for flow compensation
    - Target temperature tracking for PID reset on setpoint change
    
    Features:
    - Priority weighting: Lower priority zones aggregate demands to prevent cycling
    - TRV mitigation: Tracks valve opening % and compensates for valve closing
    - PID export: Exposes P, I, D components to Home Assistant for monitoring
    - Setpoint change handling: Resets integral on target temp changes
    
    The MasterController uses this zone's PID output if it has the maximum demand.
    """

    def __init__(self, entity_id: str, name: str, floor_area_m2: float = 0.0,
                 priority: float = 1.0, trv_entity_id: Optional[str] = None) -> None:
        """
        Initialize zone wrapper for a Home Assistant climate entity.
        
        Args:
            entity_id: Home Assistant climate entity ID (e.g., "climate.bedroom")
            name: Human-readable zone name (e.g., "Master Bedroom")
            floor_area_m2: Floor area in square meters (optional, for energy calculations)
            priority: Zone priority weight (0.0-1.0, default 1.0 for normal priority)
                     Lower priority zones have aggregated demands to prevent boiler cycling
                     Example: priority=0.1 means zone only triggers boiler if very cold
            trv_entity_id: Optional TRV valve entity ID to track opening percentage
                          Used to mitigate TRV closing effects on thermostat control
                          Home Assistant exposes this as number.entity_opening_percent
        """
        self.entity_id = entity_id
        self.name = name
        self.floor_area_m2 = floor_area_m2
        self.priority = max(0.0, min(1.0, priority))  # Clamp priority to 0.0-1.0
        self.trv_entity_id = trv_entity_id
        
        # ========== Runtime State ==========
        # Current temperature error: target - current (positive = too cold)
        self.current_error: float = 0.0
        
        # PID integral accumulator: sum of (error * time_delta) over time
        # Used for long-term correction of persistent errors
        self.pid_integral_sum: float = 0.0
        
        # Previous error used for derivative calculation (rate of change)
        self.last_error: float = 0.0
        
        # Unix timestamp of last state update (for time_delta calculations)
        self.last_update_time: float = 0.0
        
        # Is this zone actively calling for heat? (HVAC action == 'heating')
        self.is_demanding_heat: bool = False
        
        # Current target temperature from Home Assistant thermostat
        self.target_temp: float = 0.0
        
        # Previous target temperature (used to detect setpoint changes)
        # When target changes significantly, reset PID integral to avoid wind-up
        self.last_target_temp: float = 0.0
        
        # Current room temperature from Home Assistant climate entity
        self.current_temp: float = 0.0
        
        # TRV valve opening percentage (0-100%, updated from Home Assistant)
        # TRVs close in 25% steps: 100%->75%->50%->25%->0%
        # We use this to compensate the error signal when valve is closing
        self.trv_opening_percent: float = 100.0
        
        # PID output history for Home Assistant export
        # Allows monitoring/debugging of controller behavior
        self.last_pid_output: float = 0.0
        self.last_pid_p: float = 0.0  # Proportional component
        self.last_pid_i: float = 0.0  # Integral component
        self.last_pid_d: float = 0.0  # Derivative component
        
        _LOGGER.info(
            "Zone %s initialized (entity_id=%s, area=%.1f m², priority=%.2f%s)", 
            self.name, self.entity_id, self.floor_area_m2, self.priority,
            f", TRV={trv_entity_id}" if trv_entity_id else ""
        )

    def update_from_state(self, new_state) -> None:
        """
        Update zone state from Home Assistant climate entity.
        
        Reads:
        - current_temperature: Actual room temperature
        - temperature: Target/setpoint temperature
        - hvac_action: Current HVAC mode (heating, idle, cooling, off)
        
        Updates:
        - Temperature error: target - current
        - PID state (integral, derivative)
        - Heating demand flag
        - Detects target temperature changes and resets PID integral
        
        Args:
            new_state: Home Assistant climate entity state object
        """
        if not new_state:
            _LOGGER.debug("Zone %s: update_from_state called with None state", self.name)
            return

        try:
            # Extract temperature readings from climate entity attributes
            current_temp = float(new_state.attributes.get('current_temperature', 0.0))
            target_temp = float(new_state.attributes.get('temperature', 0.0))
            hvac_action = new_state.attributes.get('hvac_action', 'off')
        except (ValueError, TypeError) as e:
            _LOGGER.warning("Data error for zone %s: %s", self.name, e)
            return

        self.current_temp = current_temp
        
        # ===== TARGET TEMPERATURE CHANGE HANDLING =====
        # When user changes the setpoint, reset PID integral to avoid wind-up
        # Example: User sets temp from 20°C to 23°C - don't carry old integral forward
        if target_temp != self.last_target_temp and self.last_target_temp > 0:
            _LOGGER.info(
                "Zone %s: Target temperature changed from %.1f°C to %.1f°C - resetting PID integral",
                self.name, self.last_target_temp, target_temp
            )
            # Reset integral accumulator on setpoint change
            self.pid_integral_sum = 0.0
            self.last_error = 0.0
        
        self.target_temp = target_temp
        self.last_target_temp = target_temp
        
        # Zone is "demanding heat" only if HVAC is actively heating
        self.is_demanding_heat = (hvac_action == 'heating')

        # Calculate temperature error and time elapsed since last update
        new_error = target_temp - current_temp
        time_now = time.time()
        time_delta = time_now - self.last_update_time if self.last_update_time else 0

        # Log detailed state change for debugging
        _LOGGER.debug(
            "Zone %s state update: current=%.1f°C, target=%.1f°C, error=%.1f°C, action=%s, time_delta=%.1fs",
            self.name, current_temp, target_temp, new_error, hvac_action, time_delta
        )

        # Update PID terms when zone is actively heating
        if self.is_demanding_heat:
            
            # ===== Integral Term (I) =====
            # Accumulate error over time for long-term correction
            # I_sum tracks persistent errors that need integral boost
            self.pid_integral_sum += new_error * time_delta
            
            # Clamp integral to prevent wind-up (saturation)
            # Wind-up occurs when integrator saturates and continues accumulating
            # during startup or after sustained errors
            self.pid_integral_sum = max(-10000.0, min(10000.0, self.pid_integral_sum))
            
            # ===== Derivative Term (D) Setup =====
            # Store current error for next update's derivative calculation
            self.last_error = self.current_error
            
            _LOGGER.debug(
                "Zone %s PID state: P_error=%.1f°C, I_sum=%.2f, D_prev=%.1f°C",
                self.name, new_error, self.pid_integral_sum, self.last_error
            )
        else:
            # Zone not heating: log state but don't accumulate errors
            _LOGGER.debug(
                "Zone %s: HVAC action is %s (not heating), demand metrics reset", 
                self.name, hvac_action
            )
            
        self.current_error = new_error
        self.last_update_time = time_now

    def get_demand_metric(self) -> float:
        """
        Return this zone's heating demand metric with priority weighting and TRV mitigation.
        
        Calculation:
        1. Base demand: current error only if zone is actively heating and error > 0
        2. TRV mitigation: Boost error if TRV valve is closing (opening_percent < 100%)
           - Example: 50% opening -> boost error by 2x to compensate for reduced flow
        3. Priority weighting: Multiply by zone priority (0.0-1.0)
           - Priority=1.0: Full demand (important zone)
           - Priority=0.5: Half demand (medium importance)
           - Priority=0.1: 10% demand (low importance, aggregated)
        
        Returns:
            float: Weighted demand metric used by MasterController to find max-demand zone
            
        Used by MasterController to find max-demand zone for boiler command.
        """
        if not self.is_demanding_heat or self.current_error <= 0:
            return 0.0
        
        # Start with base error signal
        demand = self.current_error
        
        # ===== TRV MITIGATION =====
        # When TRV valve is closing, boost error signal to compensate
        # TRVs close in 25% steps, creating flow reduction
        # Closing pattern: 100% -> 75% -> 50% -> 25% -> 0%
        # 
        # Mitigation: Error_boosted = Error / (opening_percent / 100)
        # Examples:
        # - Opening 100% (fully open): boost = 1.0 (no boost)
        # - Opening 75% (1 step closed): boost = 1.33 (33% boost)
        # - Opening 50% (half closed): boost = 2.0 (double error)
        # - Opening 25% (mostly closed): boost = 4.0 (quadruple error)
        if self.trv_opening_percent < 100.0 and self.trv_opening_percent > 0:
            trv_boost = 100.0 / self.trv_opening_percent
            demand *= trv_boost
            _LOGGER.debug(
                "Zone %s: TRV mitigation - opening=%.0f%%, boost=%.2f, boosted_error=%.2f°C",
                self.name, self.trv_opening_percent, trv_boost, demand
            )
        
        # Note: Priority-based decision is handled in MasterController via aggregation:
        # - High-priority zones (priority > 0.5) can trigger boiler individually
        # - Low-priority zones (priority <= 0.5) require 2+ demanding to trigger boiler
        # This prevents single low-importance zones from cycling boiler unnecessarily
        
        return demand

    def calculate_pid_output(self, time_delta: float) -> float:
        """
        Calculate full PID output for this zone's heating demand with component export.
        
        PID Algorithm:
        - P (Proportional): Immediate response to current error
        - I (Integral): Accumulated correction for persistent error
        - D (Derivative): Dampening based on error rate of change
        
        Output = P + I + D
        
        This output is used by MasterController if this zone has max demand.
        The output is mapped to physical boiler flow temperature (40 + PID_output).
        
        Components are stored for Home Assistant export/monitoring.
        
        Args:
            time_delta: Time elapsed since last PID calculation (seconds)
            
        Returns:
            float: Total PID output (used to boost flow temperature)
        """
        
        # ===== P (Proportional) Term =====
        # Proportional to current error: larger error -> larger output
        # Provides immediate response to temperature mismatch
        P = self.current_error * KP
        
        # ===== I (Integral) Term =====
        # Proportional to accumulated error over time
        # Corrects persistent errors that P alone cannot fix
        # Example: If zone stays 1°C below target for 1 hour, integral builds up
        I = self.pid_integral_sum * KI
        
        # ===== D (Derivative) Term =====
        # Proportional to rate of error change
        # Provides damping: prevents overshoot when error is decreasing rapidly
        # Example: If temperature rising quickly, D reduces boost to prevent overshoot
        D = 0.0
        if time_delta > 0:
            error_rate_of_change = (self.current_error - self.last_error) / time_delta
            D = error_rate_of_change * KD
        
        pid_output = P + I + D
        
        # Store components for Home Assistant export
        self.last_pid_p = P
        self.last_pid_i = I
        self.last_pid_d = D
        self.last_pid_output = pid_output
        
        _LOGGER.debug(
            "Zone %s PID calculation: P=%.2f, I=%.2f, D=%.2f, total=%.2f (time_delta=%.1fs)",
            self.name, P, I, D, pid_output, time_delta
        )
            
        return pid_output
    
    def update_trv_opening(self, opening_percent: float) -> None:
        """
        Update TRV valve opening percentage from Home Assistant entity.
        
        TRV valves report their opening percentage (0-100%) which indicates
        how much they're restricting flow. As thermostats reach setpoint,
        TRVs progressively close in 25% steps.
        
        This method updates our internal TRV state which is used by
        get_demand_metric() to boost error signal when valve is closing.
        
        Args:
            opening_percent: TRV opening percentage (0-100%)
        """
        if opening_percent != self.trv_opening_percent:
            old_opening = self.trv_opening_percent
            self.trv_opening_percent = max(0.0, min(100.0, opening_percent))
            
            if old_opening != self.trv_opening_percent:
                _LOGGER.debug(
                    "Zone %s: TRV opening changed from %.0f%% to %.0f%%",
                    self.name, old_opening, self.trv_opening_percent
                )
    
    def export_pid_state(self) -> dict:
        """
        Export zone's PID state for Home Assistant monitoring.
        
        Returns a dictionary with all relevant state information that can be
        stored as Home Assistant attributes or sensor values for visualization
        and debugging.
        
        Returns:
            dict: Zone state including temperature, error, PID components, priority, TRV %
        """
        return {
            "current_temperature": round(self.current_temp, 2),
            "target_temperature": round(self.target_temp, 2),
            "temperature_error": round(self.current_error, 2),
            "pid_proportional": round(self.last_pid_p, 2),
            "pid_integral": round(self.last_pid_i, 2),
            "pid_derivative": round(self.last_pid_d, 2),
            "pid_output": round(self.last_pid_output, 2),
            "priority": round(self.priority, 2),
            "is_demanding_heat": self.is_demanding_heat,
            "integral_sum": round(self.pid_integral_sum, 2),
            "trv_opening_percent": round(self.trv_opening_percent, 1) if self.trv_entity_id else None,
        }