import logging
import time
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.event import async_track_state_change_event
from .zone_wrapper import ZoneWrapper # Import the zone class

_LOGGER = logging.getLogger(__name__)

# Constants for OpenTherm control
OPEN_THERM_FLOW_TEMP_ENTITY = "number.opentherm_flow_temp"
MIN_FLOW_TEMP = 5.0  # Boiler OFF signal
MAX_FLOW_TEMP = 80.0 # Maximum allowed flow temperature

class MasterController:
    """Manages multi-zone demand, runs PID, and controls the OpenTherm flow temperature."""

    def __init__(self, hass: HomeAssistant, zone_configs: list[dict]) -> None:
        self.hass = hass
        self.zones: dict[str, ZoneWrapper] = {}
        
        # Instantiate ZoneWrapper objects from configuration
        for config in zone_configs:
            entity_id = config['entity_id']
            zone_name = config.get('name', entity_id)
            self.zones[entity_id] = ZoneWrapper(
                entity_id=entity_id, 
                name=zone_name,
                floor_area_m2=config.get('area', 0.0) # Example of metadata
            )
        
        # List of all entities to monitor for HA event listener
        self.monitored_entity_ids = list(self.zones.keys())

    async def async_start_listening(self):
        """Set up all state change listeners."""
        _LOGGER.info("MasterController starting to listen to %s zones.", len(self.zones))
        
        # Listen for any state change on the monitored thermostat entities
        # We rely on the event to provide the state object directly
        self.hass.helpers.event.async_track_state_change_event(
            self.monitored_entity_ids,
            self._async_hvac_demand_change
        )
        
    async def _async_hvac_demand_change(self, event) -> None:
        """Event hook: Called when a monitored thermostat's state changes."""
        entity_id = event.data.get('entity_id')
        new_state = event.data.get('new_state')
        
        zone = self.zones.get(entity_id)
        if zone and new_state:
            zone.update_from_state(new_state)
            
        # Trigger the core logic calculation
        await self._calculate_and_command()

    async def _calculate_and_command(self) -> None:
        """Core API method: Finds max demand, calculates flow temp, and commands OpenTherm."""
        
        # 1. Find the MOST DEMANDING zone (the one with the largest positive error)
        max_demand_zone = None
        max_error = 0.0
        time_delta = 0.0
        
        for zone in self.zones.values():
            if zone.is_demanding_heat and zone.current_error > max_error:
                max_error = zone.current_error
                max_demand_zone = zone
                time_delta = time.time() - zone.last_update_time if zone.last_update_time else 0.0

        # 2. Command the OpenTherm based on the demand
        if max_demand_zone:
            # Use the PID output from the max demand zone
            pid_output = max_demand_zone.calculate_pid_output(time_delta)
            
            # Map PID output (e.g., a power level) to an actual flow temperature (e.g., 30C to 70C)
            # This mapping function is part of the final PID tuning
            required_flow_temp = max(MIN_FLOW_TEMP, min(MAX_FLOW_TEMP, 40.0 + pid_output)) 
            
            await self.async_set_opentherm_flow_temp(required_flow_temp)
            _LOGGER.debug("Boiler ON. Max demand from %s. Flow Temp: %.1fC", max_demand_zone.name, required_flow_temp)
        else:
            # All zones satisfied or off
            await self.async_set_opentherm_flow_temp(MIN_FLOW_TEMP)
            _LOGGER.debug("Boiler OFF. All zones satisfied.")


    async def async_set_opentherm_flow_temp(self, flow_temp: float) -> None:
        """Calls the Home Assistant service to set the flow temperature on the ESPHome device."""
        
        # Clamp flow_temp to the safe limits
        final_temp = max(MIN_FLOW_TEMP, min(MAX_FLOW_TEMP, flow_temp))
        
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": OPEN_THERM_FLOW_TEMP_ENTITY, "value": final_temp},
            blocking=False,
        )