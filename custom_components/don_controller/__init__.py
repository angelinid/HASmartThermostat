# __init__.py (Modern Component Entry Point)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .master_controller import MasterController

DOMAIN = "my_opentherm_controller"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MasterController from a config entry (UI setup)."""
    
    # 1. Load configuration from the UI entry
    zone_configs = entry.data.get("zones", [])
    
    # 2. Instantiate and store the controller
    controller = MasterController(hass, zone_configs)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller
    
    # 3. Start listening to events
    await controller.async_start_listening()

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of a config entry."""
    # This is required to clean up event listeners when the component is removed via the UI.
    # The listeners would need to be stored in the controller instance for cleanup.
    return True
