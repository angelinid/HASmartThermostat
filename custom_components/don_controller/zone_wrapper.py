import time
import logging
# Assuming PID constants Kp, Ki, Kd are defined globally or passed in
KP = 0.5 
KI = 0.01
KD = 0.1

_LOGGER = logging.getLogger(__name__)

class ZoneWrapper:
    """Wraps a Home Assistant thermostat entity, storing config and runtime metadata."""

    def __init__(self, entity_id: str, name: str, floor_area_m2: float = 0.0) -> None:
        self.entity_id = entity_id
        self.name = name
        self.floor_area_m2 = floor_area_m2
        
        # --- Runtime Metadata (PID State) ---
        self.current_error: float = 0.0
        self.pid_integral_sum: float = 0.0  
        self.last_error: float = 0.0        
        self.last_update_time: float = 0.0
        self.is_demanding_heat: bool = False
        self.target_temp: float = 0.0

    def update_from_state(self, new_state) -> None:
        """
        Updates the internal state based on the latest HA state object.
        Reads core attributes and updates PID-related metadata.
        """
        if not new_state:
            return

        try:
            current_temp = float(new_state.attributes.get('current_temperature', 0.0))
            target_temp = float(new_state.attributes.get('temperature', 0.0))
            hvac_action = new_state.attributes.get('hvac_action', 'off')
        except (ValueError, TypeError) as e:
            _LOGGER.warning("Data error for zone %s: %s", self.name, e)
            return

        self.target_temp = target_temp
        self.is_demanding_heat = (hvac_action == 'heating')

        # --- Calculate and Store Error ---
        new_error = target_temp - current_temp
        time_now = time.time()
        time_delta = time_now - self.last_update_time if self.last_update_time else 0

        # Update PID terms only if there's a significant change or we are heating
        if self.is_demanding_heat:
            
            # Integral Term (I)
            self.pid_integral_sum += new_error * time_delta
            # Clamp integral sum to prevent wind-up
            self.pid_integral_sum = max(-10.0, min(10.0, self.pid_integral_sum)) 
            
            # Derivative Term (D) setup
            self.last_error = self.current_error
            
        self.current_error = new_error
        self.last_update_time = time_now

    def get_demand_metric(self) -> float:
        """Returns the primary error metric used by the MasterController."""
        # Only return the positive error if the zone is actively demanding heat.
        return self.current_error if self.is_demanding_heat and self.current_error > 0 else 0.0

    def calculate_pid_output(self, time_delta: float) -> float:
        """Calculates the full PID output for this zone (used if this zone is max demand)."""
        P = self.current_error * KP
        I = self.pid_integral_sum * KI
        
        D = 0.0
        if time_delta > 0:
            # Derivative (D) term: Rate of change of the error
            D = (self.current_error - self.last_error) / time_delta * KD
            
        return P + I + D