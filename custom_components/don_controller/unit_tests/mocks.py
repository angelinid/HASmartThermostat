import time
from unittest.mock import MagicMock

# --- Fixed Starting Time for Reproducible PID Testing ---
FIXED_TIME_START = 1672531200.0 

# =========================================================
# MOCKING CLASSES
# =========================================================

class MockState:
    """Mimics the Home Assistant State object."""
    def __init__(self, entity_id, attributes=None):
        self.entity_id = entity_id
        # Attributes is a dictionary containing all state details (temp, hvac_action, etc.)
        self.attributes = attributes if attributes is not None else {}

    def __repr__(self):
        return f"<MockState entity_id='{self.entity_id}' attrs={self.attributes}>"


class MockHASS:
    """Mimics the Home Assistant Core object for service calls."""
    def __init__(self):
        # Mock the service call interface
        self.services = MagicMock()
        # Mock the event tracking setup
        self.helpers = MagicMock() 

    async def async_call(self, domain, service, service_data, blocking):
        """Stub for hass.services.async_call, records the call."""
        # Use the MagicMock to record the arguments
        return self.services.async_call(domain, service, service_data, blocking)

# =========================================================
# HELPER FUNCTIONS
# =========================================================

def create_mock_event(entity_id, current_temp, target_temp, hvac_action):
    """Helper to create a mock event dictionary for controller input."""
    state = MockState(entity_id, attributes={
        'current_temperature': current_temp, 
        'temperature': target_temp, 
        'hvac_action': hvac_action
    })
    return MagicMock(data={'entity_id': entity_id, 'new_state': state})
