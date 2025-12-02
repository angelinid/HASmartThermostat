import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigType, DiscoveryInfoType

# Import the MasterController class
from .master_controller import MasterController

_LOGGER = logging.getLogger(__name__)

DOMAIN = "my_opentherm_controller" # Define your component's domain

async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Master Controller platform."""
    _LOGGER.info("Starting OpenTherm MasterController setup...")
    
    # Example configuration structure (This is what you'd define in configuration.yaml)
    # This list defines which thermostats your MasterController should monitor.
    zone_configs = config.get("zones", [
        {"entity_id": "climate.bedroom_thermostat", "name": "Bedroom"},
        {"entity_id": "climate.kitchen_thermostat", "name": "Kitchen", "area": 15.0},
    ])

    if not zone_configs:
        _LOGGER.error("No zones defined for the MasterController.")
        return

    # Create and start the MasterController instance
    controller = MasterController(hass, zone_configs)
    
    # Store the controller instance globally (optional, but useful for debugging/service calls)
    hass.data[DOMAIN] = controller 
    
    # Start the event listeners
    await controller.async_start_listening()

    _LOGGER.info("OpenTherm MasterController is now active and monitoring zones.")