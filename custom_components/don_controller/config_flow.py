import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

_LOGGER = logging.getLogger(__name__)

DOMAIN = "my_opentherm_controller"

# Configuration keys
CONF_ENTITY_ID = "entity_id"  # Climate entity to control
CONF_NAME = "name"            # Zone name
CONF_AREA = "area"            # Zone floor area in m²
CONF_PRIORITY = "priority"    # Zone priority (0.0-1.0)
CONF_TRV_ENTITY_ID = "trv_entity_id"  # Optional TRV valve opening % entity

# Define the data schema for the zone configuration
# This defines the structure of data for each zone
DATA_SCHEMA = vol.Schema({
    # Use EntitySelector for searchable dropdown (filter to climate entities)
    vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
        selector.EntitySelectorConfig(domain="climate")
    ),
    
    vol.Optional(CONF_NAME): str,
    vol.Optional(CONF_AREA, default=0.0): vol.Coerce(float),
    
    # Priority weighting: controls how much this zone influences boiler decisions
    # 1.0 = normal (default), 0.5 = half importance, 0.1 = low importance
    vol.Optional(CONF_PRIORITY, default=1.0): vol.All(
        vol.Coerce(float), 
        vol.Range(min=0.0, max=1.0)
    ),
    
    # Optional TRV valve opening % entity for mitigation of closing valves
    # Typically a number entity that reports 0-100% opening
    vol.Optional(CONF_TRV_ENTITY_ID): selector.EntitySelector(
        selector.EntitySelectorConfig(domain="number")
    ),
})


class OpenThermConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Handle config flow for the OpenTherm MasterController.
    
    Guides users through:
    1. Adding zones (climate entities)
    2. Setting zone properties (name, area, priority)
    3. Optional TRV valve opening tracking
    4. Deciding if they want to add more zones
    """

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    # Internal list to store zone configurations as the user adds them
    _zones_config = [] 

    async def async_step_user(self, user_input=None):
        """
        Handle the initial step when the user adds the component.
        
        Presents form to:
        - Select a climate entity (required)
        - Enter zone name (optional, defaults to entity name)
        - Enter floor area in m² (optional)
        - Set priority weight 0.0-1.0 (optional, defaults to 1.0)
        - Select TRV valve opening entity (optional)
        """
        
        errors = {}

        if user_input is not None:
            # Validate and store the configuration
            self._zones_config.append(user_input)
            
            # Ask user if they want to add another zone
            return await self.async_step_add_another()
            
        # Initial form shown to the user
        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
            description_placeholders={"count": len(self._zones_config)}
        )

    async def async_step_add_another(self, user_input=None):
        """
        Ask the user if they want to add another zone.
        
        After successful zone configuration, offers choice to:
        - Add another zone (returns to user step)
        - Finish (creates config entry with all zones)
        """
        
        if user_input is not None:
            if user_input.get("add_another"):
                # If yes, go back to the user step to collect data for the next zone
                return await self.async_step_user()
            
            # If no, finalize the configuration with all collected zones
            return self.async_create_entry(
                title="OpenTherm MasterController",
                data={"zones": self._zones_config},  # Save the final list of zones
            )

        # Form to ask if more zones are needed
        return self.async_show_form(
            step_id="add_another",
            data_schema=vol.Schema({
                vol.Required("add_another", default=True): bool
            }),
            description_placeholders={"current_count": len(self._zones_config)}
        )
