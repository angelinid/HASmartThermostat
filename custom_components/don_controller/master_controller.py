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
from typing import TYPE_CHECKING, Optional

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
                - priority: Zone priority weight 0.0-1.0 (optional, default 1.0)
                  * 1.0 = normal importance, full demand counted
                  * 0.5 = medium importance, half demand
                  * 0.1 = low importance, aggregated to prevent cycling
                - trv_entity_id: TRV valve opening % entity for mitigation (optional)
        """
        self.hass = hass
        self.zones: dict[str, ZoneWrapper] = {}
        
        _LOGGER.info("MasterController initializing with %d zones", len(zone_configs))
        
        # Instantiate ZoneWrapper for each configured zone
        for config in zone_configs:
            entity_id = config['entity_id']
            zone_name = config.get('name', entity_id)
            area = config.get('area', 0.0)
            priority = config.get('priority', 1.0)  # Default: normal priority
            trv_entity_id = config.get('trv_entity_id', None)  # Optional TRV tracking
            
            # Create zone wrapper with PID controller and priority
            self.zones[entity_id] = ZoneWrapper(
                entity_id=entity_id, 
                name=zone_name,
                floor_area_m2=area,
                priority=priority,
                trv_entity_id=trv_entity_id
            )
            _LOGGER.info(
                "MasterController: Registered zone '%s' (%s, area=%.1f m², priority=%.2f%s)", 
                zone_name, entity_id, area, priority,
                f", TRV tracking" if trv_entity_id else ""
            )
        
        # List of all entities to monitor for Home Assistant state change events
        # This includes both climate entities and optional TRV entities
        self.monitored_entity_ids = list(self.zones.keys())
        
        # Add TRV entities to monitoring list
        for zone in self.zones.values():
            if zone.trv_entity_id:
                self.monitored_entity_ids.append(zone.trv_entity_id)

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
        Event handler: Called when any monitored entity's state changes.
        
        Handles both climate entity changes and TRV valve opening updates:
        - Climate entity change: Updates temperature, target, HVAC action
        - TRV opening change: Updates valve opening % for demand mitigation
        
        After any state change, triggers boiler control logic recalculation.
        
        Args:
            event: Home Assistant state change event containing entity_id and new_state
        """
        entity_id = event.data.get('entity_id')
        new_state = event.data.get('new_state')
        
        _LOGGER.debug("State change event received for entity_id=%s", entity_id)
        
        # Check if this is a climate entity update or TRV update
        zone = self.zones.get(entity_id)
        if zone and new_state:
            # Climate entity update
            zone.update_from_state(new_state)
            _LOGGER.debug("Zone '%s' updated from state", zone.name)
        else:
            # Check if this is a TRV entity update for any zone
            trv_updated = False
            for zone in self.zones.values():
                if zone.trv_entity_id == entity_id and new_state:
                    try:
                        # Extract TRV opening percentage from state
                        trv_opening = float(new_state.state)
                        zone.update_trv_opening(trv_opening)
                        trv_updated = True
                        _LOGGER.debug(
                            "Zone '%s': TRV opening updated to %.0f%%",
                            zone.name, trv_opening
                        )
                    except (ValueError, TypeError) as e:
                        _LOGGER.warning(
                            "Error reading TRV opening from %s: %s",
                            entity_id, e
                        )
                    break
            
            if not zone and not trv_updated:
                _LOGGER.warning("Unknown entity_id received: %s", entity_id)
            
        # Recalculate boiler command based on all zones' current states
        await self._calculate_and_command()

    async def _calculate_and_command(self) -> None:
        """
        Core control algorithm: Find max demand zone with priority aggregation.
        
        Algorithm:
        1. Separate zones by priority level (high vs low priority)
        2. For HIGH priority zones: any one can trigger boiler
        3. For LOW priority zones: require at least 2 demanding to trigger boiler (prevent cycling)
        4. Find zone with maximum demand among eligible zones
        5. Use that zone's PID controller to calculate required boiler output
        6. Map PID output to OpenTherm flow temperature
        7. Command the boiler via Home Assistant service call
        
        Priority Aggregation:
        - High priority (priority > 0.5): Single zone can trigger boiler
        - Low priority (priority <= 0.5): At least 2 zones must demand heat to trigger
        - This prevents low-importance rooms (e.g., guest room, garage) from unnecessarily
          turning on the boiler when only one zone needs heat
        - Balances comfort with energy efficiency
        
        TRV Mitigation:
        - Error boosted when valve is closing (lower opening % = higher boost)
        - Compensates for TRV restricting flow as it approaches setpoint
        """
        
        # Step 1: Collect zones by priority level
        high_priority_zones = []
        low_priority_zones = []
        
        for zone in self.zones.values():
            if zone.is_demanding_heat:
                if zone.priority > 0.5:
                    high_priority_zones.append(zone)
                else:
                    low_priority_zones.append(zone)
        
        # Log zone grouping for monitoring
        _LOGGER.debug(
            "Priority aggregation: %d high-priority demanding, %d low-priority demanding",
            len(high_priority_zones), len(low_priority_zones)
        )
        
        # Log detailed status of all zones
        for zone in self.zones.values():
            demand_metric = zone.get_demand_metric()
            priority_group = "high" if zone.priority > 0.5 else "low"
            _LOGGER.debug(
                "Zone '%s': [%s-priority] demanding=%s, error=%.1f°C, priority=%.2f, demand_metric=%.2f°C%s",
                zone.name, priority_group, zone.is_demanding_heat, zone.current_error, 
                zone.priority, demand_metric,
                f", TRV={zone.trv_opening_percent:.0f}%" if zone.trv_entity_id else ""
            )
        
        # Step 2: Determine if boiler should be activated
        # HIGH priority: any single zone demanding heat can trigger
        # LOW priority: require at least 2 zones demanding to prevent cycling
        boiler_demand_eligible = []
        
        # Add all high-priority demanding zones
        boiler_demand_eligible.extend(high_priority_zones)
        
        # Add low-priority zones only if at least 2 are demanding
        if len(low_priority_zones) >= 2:
            boiler_demand_eligible.extend(low_priority_zones)
            _LOGGER.debug(
                "Low-priority aggregation: %d zones demanding, threshold met (≥2), including in boiler decision",
                len(low_priority_zones)
            )
        elif len(low_priority_zones) == 1:
            _LOGGER.debug(
                "Low-priority aggregation: %d zone demanding, threshold NOT met (<2), excluding from boiler decision",
                len(low_priority_zones)
            )
        
        # Step 3: Find the zone with maximum demand among eligible zones
        max_demand_zone = None
        max_demand = 0.0
        time_delta = 0.0
        
        for zone in boiler_demand_eligible:
            demand_metric = zone.get_demand_metric()
            if demand_metric > max_demand:
                max_demand = demand_metric
                max_demand_zone = zone
                time_delta = time.time() - zone.last_update_time if zone.last_update_time else 0.0

        # Step 4: Command boiler based on max demand zone
        if max_demand_zone:
            # Calculate PID output from max demand zone's error
            pid_output = max_demand_zone.calculate_pid_output(time_delta)
            
            # Map PID output to physical flow temperature
            # Formula: flow_temp = base_temp (40°C) + PID_boost
            # Higher error -> larger PID boost -> higher flow temperature
            required_flow_temp = max(MIN_FLOW_TEMP, min(MAX_FLOW_TEMP, 40.0 + pid_output))
            
            await self.async_set_opentherm_flow_temp(required_flow_temp)
            
            # Determine boiler trigger reason (high-priority vs low-priority aggregation)
            trigger_reason = "high-priority demand"
            if max_demand_zone in low_priority_zones:
                trigger_reason = f"low-priority aggregation ({len(low_priority_zones)} zones)"
            
            _LOGGER.info(
                "Boiler ON [%s]. Max demand from zone '%s': error=%.1f°C, priority=%.2f, "
                "demand=%.2f°C, PID=%.2f, flow_temp=%.1f°C",
                trigger_reason, max_demand_zone.name, max_demand_zone.current_error, 
                max_demand_zone.priority, max_demand, pid_output, required_flow_temp
            )
        else:
            # No eligible zones demanding heat: turn boiler OFF
            await self.async_set_opentherm_flow_temp(MIN_FLOW_TEMP)
            
            if len(low_priority_zones) > 0:
                _LOGGER.info(
                    "Boiler OFF. No high-priority zones demanding. Low-priority zones: %d demanding (need ≥2)",
                    len(low_priority_zones)
                )
            else:
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

    def get_controller_state(self) -> dict:
        """
        Export complete controller state for Home Assistant monitoring.
        
        Returns controller-wide metrics and per-zone PID information that can be
        displayed in Home Assistant UI or used for automations.
        
        Returns:
            dict: Controller state with zones array containing PID data for each zone
        """
        zones_state = []
        for zone in self.zones.values():
            zones_state.append({
                "name": zone.name,
                "entity_id": zone.entity_id,
                "state": zone.export_pid_state()
            })
        
        return {
            "zones": zones_state,
            "zone_count": len(self.zones)
        }
    
    def get_zone_state(self, entity_id: str) -> Optional[dict]:
        """
        Export state for a specific zone.
        
        Useful for creating Home Assistant template sensors that display
        PID components, priority, TRV opening %, etc.
        
        Args:
            entity_id: Climate entity ID of the zone
            
        Returns:
            dict: Zone's PID state, or None if zone not found
        """
        zone = self.zones.get(entity_id)
        if zone:
            return zone.export_pid_state()
        return None