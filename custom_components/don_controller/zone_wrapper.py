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
Zone Wrapper: Individual Zone PID Controller

Each zone (room) has a ZoneWrapper that:
1. Reads current/target temperature from Home Assistant climate entity
2. Calculates temperature error (target - current)
3. Implements PID algorithm to calculate zone's heating demand
4. Provides demand metric to MasterController for prioritization

PID Controller Components:
- P (Proportional): Immediate response proportional to error
  P = error * KP (0.5)
  
- I (Integral): Long-term correction for persistent error
  I_sum accumulates error * time_delta
  I = I_sum * KI (0.01)
  
- D (Derivative): Damping based on error rate of change
  D = (error_now - error_prev) / time_delta * KD (0.1)

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
    - Zone metadata (name, entity_id, floor area)
    - Temperature readings and error calculation
    - PID state (integral sum, last error, update time)
    - Heating demand determination (is zone actively heating?)
    
    The MasterController uses this zone's PID output if it has the maximum demand.
    """

    def __init__(self, entity_id: str, name: str, floor_area_m2: float = 0.0) -> None:
        """
        Initialize zone wrapper for a Home Assistant climate entity.
        
        Args:
            entity_id: Home Assistant climate entity ID (e.g., "climate.bedroom")
            name: Human-readable zone name (e.g., "Master Bedroom")
            floor_area_m2: Floor area in square meters (optional, for energy calculations)
        """
        self.entity_id = entity_id
        self.name = name
        self.floor_area_m2 = floor_area_m2
        
        # ========== Runtime State ==========
        # Current temperature error: target - current
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
        
        # Current room temperature from Home Assistant climate entity
        self.current_temp: float = 0.0
        
        _LOGGER.info(
            "Zone %s initialized (entity_id=%s, area=%.1f m²)", 
            self.name, self.entity_id, self.floor_area_m2
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
        self.target_temp = target_temp
        
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
        Return this zone's heating demand metric.
        
        Returns current error only if:
        1. Zone's HVAC is actively heating, AND
        2. Error is positive (temperature below target)
        
        Returns:
            float: Current error if heating, 0.0 otherwise
            
        Used by MasterController to find max-demand zone.
        """
        return self.current_error if self.is_demanding_heat and self.current_error > 0 else 0.0

    def calculate_pid_output(self, time_delta: float) -> float:
        """
        Calculate full PID output for this zone's heating demand.
        
        PID Algorithm:
        - P (Proportional): Immediate response to current error
        - I (Integral): Accumulated correction for persistent error
        - D (Derivative): Dampening based on error rate of change
        
        Output = P + I + D
        
        This output is used by MasterController if this zone has max demand.
        The output is mapped to physical boiler flow temperature (40 + PID_output).
        
        Args:
            time_delta: Time elapsed since last PID calculation (seconds)
            
        Returns:
            float: Total PID output (used to boost flow temperature)
        """
        
        # ===== P (Proportional) Term =====
        # Proportional to current error: larger error -> larger output
        P = self.current_error * KP
        
        # ===== I (Integral) Term =====
        # Proportional to accumulated error over time
        # Corrects persistent errors that P alone cannot fix
        I = self.pid_integral_sum * KI
        
        # ===== D (Derivative) Term =====
        # Proportional to rate of error change
        # Provides damping: prevents overshoot when error is decreasing rapidly
        D = 0.0
        if time_delta > 0:
            error_rate_of_change = (self.current_error - self.last_error) / time_delta
            D = error_rate_of_change * KD
        
        pid_output = P + I + D
        
        _LOGGER.debug(
            "Zone %s PID calculation: P=%.2f, I=%.2f, D=%.2f, total=%.2f (time_delta=%.1fs)",
            self.name, P, I, D, pid_output, time_delta
        )
            
        return pid_output