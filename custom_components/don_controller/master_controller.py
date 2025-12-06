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
Multi-Zone Master Controller for Home Assistant

Orchestrates heating across multiple zones (rooms) using a PID controller:
- Monitors temperature sensors in each zone
- Tracks heating demand (error = target - current)
- Selects zone with maximum demand (highest priority)
- Calculates boiler flow temperature using PID algorithm
- Commands OpenTherm boiler to maintain comfort while optimizing energy
"""

import logging
import time
from typing import TYPE_CHECKING

try:
    from .zone_wrapper import ZoneWrapper
except ImportError:
    from zone_wrapper import ZoneWrapper

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger("don_controller")

# =========================================================
# OpenTherm Control Constants
# =========================================================

# Entity ID where OpenTherm flow temperature is set (Home Assistant number entity)
OPEN_THERM_FLOW_TEMP_ENTITY = "number.opentherm_flow_temp"

# Flow temperature = 5°C is boiler OFF signal (minimum safe temperature)
MIN_FLOW_TEMP = 5.0

# Flow temperature = 80°C is maximum boiler output (safety limit)
MAX_FLOW_TEMP = 80.0


class MasterController:
    """
    Multi-zone PID heating controller.
    
    Orchestrates heating demand across multiple zones by:
    1. Monitoring each zone's temperature and target
    2. Calculating the zone with maximum demand (largest error)
    3. Running PID algorithm on that zone's error
    4. Commanding boiler flow temperature based on PID output
    
    Architecture:
    - Each zone has a ZoneWrapper with its own PID state
    - MasterController finds max-demand zone and uses its PID output
    - This approach prioritizes the most uncomfortable zone while serving all zones
    """

    def __init__(self, hass: "HomeAssistant", zone_configs: list[dict]) -> None:
        """
        Initialize the master controller with zone configuration.
        
        Args:
            hass: Home Assistant instance for service calls
            zone_configs: List of zone configurations with:
                - entity_id: Climate entity ID in Home Assistant
                - name: Human-readable zone name
                - area: Floor area in m² (optional)
        """
        self.hass = hass
        self.zones: dict[str, ZoneWrapper] = {}
        
        _LOGGER.info("MasterController initializing with %d zones", len(zone_configs))
        
        # Instantiate ZoneWrapper for each configured zone
        for config in zone_configs:
            entity_id = config['entity_id']
            zone_name = config.get('name', entity_id)
            area = config.get('area', 0.0)
            
            # Create zone wrapper with PID controller
            self.zones[entity_id] = ZoneWrapper(
                entity_id=entity_id, 
                name=zone_name,
                floor_area_m2=area
            )
            _LOGGER.info(
                "MasterController: Registered zone '%s' (%s, area=%.1f m²)", 
                zone_name, entity_id, area
            )
        
        # List of all entities to monitor for Home Assistant state change events
        self.monitored_entity_ids = list(self.zones.keys())

    async def async_start_listening(self):
        """
        Start listening for state change events on all monitored zones.
        
        Sets up Home Assistant state change event listener that will call
        _async_hvac_demand_change whenever a zone's climate entity changes state.
        """
        _LOGGER.info("MasterController starting to listen to %s zones.", len(self.zones))
        
        # Listen for state changes on all zone climate entities
        # Each state change triggers _async_hvac_demand_change event handler
        self.hass.helpers.event.async_track_state_change_event(
            self.monitored_entity_ids,
            self._async_hvac_demand_change
        )
        
    async def _async_hvac_demand_change(self, event) -> None:
        """
        Event handler: Called when any monitored zone's climate state changes.
        
        Updates the zone's temperature, target, and HVAC action from Home Assistant state,
        then triggers the core control logic to recalculate boiler demand.
        
        Args:
            event: Home Assistant state change event containing entity_id and new_state
        """
        entity_id = event.data.get('entity_id')
        new_state = event.data.get('new_state')
        
        _LOGGER.debug("State change event received for entity_id=%s", entity_id)
        
        # Update zone from new Home Assistant state
        zone = self.zones.get(entity_id)
        if zone and new_state:
            zone.update_from_state(new_state)
            _LOGGER.debug("Zone '%s' updated from state", zone.name)
        elif not zone:
            _LOGGER.warning("Unknown entity_id received: %s", entity_id)
            
        # Recalculate boiler command based on all zones' current states
        await self._calculate_and_command()

    async def _calculate_and_command(self) -> None:
        """
        Core control algorithm: Find max demand zone and command boiler.
        
        Algorithm:
        1. Iterate through all zones and find the one with maximum positive temperature error
        2. Use that zone's PID controller to calculate required boiler output
        3. Map PID output to OpenTherm flow temperature (higher error -> higher temperature)
        4. Command the boiler via Home Assistant service call
        
        Energy optimization:
        - Only the max-demand zone drives boiler command (saves energy)
        - Other zones still benefit from the heated system
        - As max zone satisfies, next highest zone takes priority
        """
        
        # Step 1: Find the zone with maximum demand (largest positive error)
        max_demand_zone = None
        max_error = 0.0
        time_delta = 0.0
        
        # Log all zone statuses for debugging/monitoring
        for zone in self.zones.values():
            demand_metric = zone.get_demand_metric()
            _LOGGER.debug(
                "Zone '%s': demanding=%s, current_error=%.1f°C, demand_metric=%.1f°C",
                zone.name, zone.is_demanding_heat, zone.current_error, demand_metric
            )
            
            # Track zone with highest demand (error) that is actively heating
            if zone.is_demanding_heat and zone.current_error > max_error:
                max_error = zone.current_error
                max_demand_zone = zone
                time_delta = time.time() - zone.last_update_time if zone.last_update_time else 0.0

        # Step 2: Command boiler based on max demand zone
        if max_demand_zone:
            # Calculate PID output from max demand zone's error
            pid_output = max_demand_zone.calculate_pid_output(time_delta)
            
            # Map PID output to physical flow temperature
            # Formula: flow_temp = base_temp (40°C) + PID_boost
            # Higher error -> larger PID boost -> higher flow temperature
            required_flow_temp = max(MIN_FLOW_TEMP, min(MAX_FLOW_TEMP, 40.0 + pid_output))
            
            await self.async_set_opentherm_flow_temp(required_flow_temp)
            _LOGGER.info(
                "Boiler ON. Max demand from zone '%s': error=%.1f°C, PID=%.2f, flow_temp=%.1f°C",
                max_demand_zone.name, max_error, pid_output, required_flow_temp
            )
        else:
            # No zone demanding heat: turn boiler OFF
            await self.async_set_opentherm_flow_temp(MIN_FLOW_TEMP)
            _LOGGER.info("Boiler OFF. All zones satisfied.")

    async def async_set_opentherm_flow_temp(self, flow_temp: float) -> None:
        """
        Command the boiler's flow temperature via OpenTherm integration.
        
        Calls Home Assistant service to set the number entity that controls
        the ESPHome OpenTherm device. Includes safety clamping to min/max.
        
        Args:
            flow_temp: Target flow temperature in °C (clamped to MIN_FLOW_TEMP .. MAX_FLOW_TEMP)
        """
        
        # Clamp to safe physical limits
        final_temp = max(MIN_FLOW_TEMP, min(MAX_FLOW_TEMP, flow_temp))
        
        _LOGGER.debug(
            "Setting OpenTherm flow temperature: requested=%.1f°C, final=%.1f°C",
            flow_temp, final_temp
        )
        
        # Call Home Assistant number service to update the entity
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": OPEN_THERM_FLOW_TEMP_ENTITY, "value": final_temp},
            blocking=False,
        )