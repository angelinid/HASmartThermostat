import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

_LOGGER = logging.getLogger(__name__)

DOMAIN = "my_opentherm_controller"

CONF_ENTITY_ID = "entity_id"  # <-- QUOTES required here
CONF_NAME = "name"            # <-- QUOTES required here
CONF_AREA = "area"            # <-- QUOTES required here

# Define the data schema for the configuration entry
# This defines the structure of data that will be saved.
DATA_SCHEMA = vol.Schema({
    # Use the EntitySelector for a searchable dropdown
    vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
        selector.EntitySelectorConfig(domain="climate") # <-- Filter to ONLY show climate entities
    ),
    
    vol.Optional(CONF_NAME): str,
    vol.Optional(CONF_AREA, default=0.0): vol.Coerce(float),
})


class OpenThermConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the OpenTherm MasterController."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    # Internal list to store zone configurations as the user adds them
    _zones_config = [] 

    async def async_step_user(self, user_input=None):
        """Handle the initial step when the user adds the component."""
        
        errors = {}

        if user_input is not None:
            # 1. Store the valid input temporarily
            self._zones_config.append(user_input)
            
            # 2. Ask the user if they want to add another zone
            return await self.async_step_add_another()
            
        # Initial form shown to the user
        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
            description_placeholders={"count": len(self._zones_config)}
        )

    async def async_step_add_another(self, user_input=None):
        """Ask the user if they want to add another zone."""
        
        if user_input is not None:
            if user_input.get("add_another"):
                # If yes, go back to the user step to collect data for the next zone
                return await self.async_step_user()
            
            # If no, finalize the configuration
            return self.async_create_entry(
                title="OpenTherm MasterController",
                data={"zones": self._zones_config}, # Save the final list of zones
            )

        # Form to ask if more zones are needed
        return self.async_show_form(
            step_id="add_another",
            data_schema=vol.Schema({
                vol.Required("add_another", default=True): bool
            }),
            description_placeholders={"current_count": len(self._zones_config)}
        )
