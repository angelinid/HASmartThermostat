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
Basic MasterController Unit Tests

Tests fundamental PID control logic with simple 2-zone setup:
- Zone demand detection and boiler control
- Flow temperature calculation from error and PID components
- Max demand zone selection algorithm
- Edge case handling (no demand, max constraints)
"""

import unittest
import asyncio
from unittest.mock import AsyncMock, patch
import sys
import os
import logging

# --- PATH ADJUSTMENT ---
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- IMPORTS ---
from unit_tests.mocks import MockHASS, FIXED_TIME_START, create_mock_event
from zone_wrapper import KP
from master_controller import MasterController, MIN_FLOW_TEMP, MAX_FLOW_TEMP, OPEN_THERM_FLOW_TEMP_ENTITY
from logging_util import LogCollector, setup_logging


# =========================================================
# TEST FIXTURE BASE - Common Setup for All Tests
# =========================================================

class BaseTestFixture(unittest.TestCase):
    """
    Base test class that provides common setup/teardown for all tests.
    
    Provides:
    - Time mocking for deterministic time-based tests
    - Standard 2-zone configuration (Bedroom, Kitchen)
    - Mock Home Assistant instance
    - Logging collection for debugging
    """
    
    def setUp(self):
        """Initialize test environment with time mocking and mock HASS instance."""
        # Mock time.time() to return consistent values across tests
        self.time_patcher = patch('time.time', return_value=FIXED_TIME_START)
        self.mock_time = self.time_patcher.start()
        
        # Standard 2-zone configuration for basic tests
        self.zone_configs = [
            {"entity_id": "climate.test_bedroom", "name": "Bedroom", "area": 10.0},
            {"entity_id": "climate.test_kitchen", "name": "Kitchen", "area": 15.0},
        ]
        self.mock_hass = MockHASS()
        
        # Setup logging to capture controller behavior
        setup_logging(level=logging.DEBUG)
        self.log_collector = LogCollector()
        self.log_collector.start_collecting()

    def tearDown(self):
        """Clean up test environment."""
        self.time_patcher.stop()
        self.log_collector.stop_collecting()


# =========================================================
# MASTER CONTROLLER BASIC FUNCTIONALITY TESTS
# =========================================================

class TestMasterController(BaseTestFixture):
    """Test core MasterController functionality."""

    def test_controller_no_demand_commands_off(self):
        """
        Test that MasterController commands boiler OFF (MIN_FLOW_TEMP)
        when no zone demands heat (all zones at target temperature).
        
        Scenario: Both zones satisfied, no heating needed.
        Expected: Flow temperature = MIN_FLOW_TEMP (boiler off signal)
        """
        self.mock_hass.services.async_call = AsyncMock() 
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Zone is at target temperature with idle HVAC status
        mock_event = create_mock_event("climate.test_bedroom", 21.0, 20.0, 'idle')

        asyncio.run(controller._async_hvac_demand_change(mock_event))
        
        # Verify service call made with minimum flow temperature
        self.mock_hass.services.async_call.assert_called_once_with(
            'number', 'set_value', 
            {'entity_id': OPEN_THERM_FLOW_TEMP_ENTITY, 'value': MIN_FLOW_TEMP}, 
            blocking=False
        )

    def test_controller_selects_max_demand_zone(self):
        """
        Test that MasterController correctly identifies and prioritizes
        the zone with the largest positive temperature error.
        
        Scenario: Kitchen has 3.0°C error, Bedroom has 1.0°C error.
        Expected: Flow temperature based on Kitchen's larger error.
        
        Calculation:
        - Kitchen error = 20 - 17 = 3.0°C
        - PID output = 3.0 * KP (0.5) = 1.5
        - Flow temp = 40.0 + 1.5 = 41.5°C
        """
        self.mock_hass.services.async_call = AsyncMock() 
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Kitchen: 3°C error (17 current, 20 target)
        kitchen_event = create_mock_event("climate.test_kitchen", 17.0, 20.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(kitchen_event))
        
        # Bedroom: 1°C error (19 current, 20 target) - lower demand
        bedroom_event = create_mock_event("climate.test_bedroom", 19.0, 20.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(bedroom_event))
        
        # Extract commanded flow temperature from service call
        args, kwargs = self.mock_hass.services.async_call.call_args
        commanded_flow_temp = args[2]['value']
        
        # Calculate expected temperature based on Kitchen's 3.0 error
        expected_flow_temp = 40.0 + (3.0 * KP)
        
        self.assertAlmostEqual(commanded_flow_temp, expected_flow_temp, delta=5.0, 
                               msg="Flow temp must reflect Kitchen's max error (3.0°C)")

    def test_controller_commands_max_flow_temp_when_needed(self):
        """
        Test that commanded flow temperature never exceeds MAX_FLOW_TEMP
        constraint, even with very large temperature errors.
        
        Scenario: Extreme error (80°C) that would normally result in flow_temp=80+40=120°C
        Expected: Flow temperature clamped at MAX_FLOW_TEMP (80.0°C)
        
        This test ensures boiler safety limits are enforced.
        """
        self.mock_hass.services.async_call = AsyncMock() 
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Extreme cold: 10°C current, 90°C target -> 80°C error
        # P component: 80 * 0.5 = 40, so unclamped would be 40+40=80°C
        mock_event = create_mock_event("climate.test_bedroom", 10.0, 90.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(mock_event))
        
        # Extract commanded flow temperature
        args, kwargs = self.mock_hass.services.async_call.call_args
        commanded_flow_temp = args[2]['value']
        
        # Verify clamping at maximum
        self.assertEqual(commanded_flow_temp, MAX_FLOW_TEMP, 
                         "Flow temperature must be clamped at MAX_FLOW_TEMP")


# =========================================================
# SUNNY DAY SCENARIO TESTS
# =========================================================

class TestMasterControllerSunnyDay(BaseTestFixture):
    """
    Test controller behavior on sunny days with solar gain.
    Simulates progressive temperature increases and boiler demand reduction.
    """
    
    def test_sunny_day_solar_gain_reduces_boiler_demand(self):
        """
        Test that boiler output reduces as solar gain increases room temperature.
        
        Scenario:
        - Morning: Cold room (16°C), high boiler demand
        - Afternoon: Solar gain warms room (19.5°C), lower demand
        
        Expected: Flow temperature decreases with reduced error
        (5.0°C error -> 1.5°C error means proportional flow temp decrease)
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Morning: High temperature error from cold start
        morning_event = create_mock_event("climate.test_bedroom", 16.0, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(morning_event))
        
        morning_args = self.mock_hass.services.async_call.call_args[0]
        morning_flow_temp = morning_args[2]['value']
        
        # Reset controller for afternoon test (avoids integral wind-up)
        self.mock_hass.services.async_call.reset_mock()
        self.mock_time.return_value += 3600
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Afternoon: Solar gain reduces error (from 5.0 to 1.5)
        afternoon_event = create_mock_event("climate.test_bedroom", 19.5, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(afternoon_event))
        
        afternoon_args = self.mock_hass.services.async_call.call_args[0]
        afternoon_flow_temp = afternoon_args[2]['value']
        
        # Verify demand reduction
        self.assertGreater(morning_flow_temp, afternoon_flow_temp,
                          "Morning demand must exceed afternoon due to solar gain")
        self.assertGreater(afternoon_flow_temp, MIN_FLOW_TEMP,
                          "Afternoon must still command heating (not fully satisfied)")
    
    def test_sunny_day_zone_priority_shift(self):
        """
        Test that max demand zone selection dynamically shifts as conditions change.
        
        Scenario:
        - Morning: Bedroom coldest (16°C vs Kitchen 18°C)
        - Mid-morning: Bedroom warms to 20°C, Kitchen becomes max demand (17°C)
        
        Expected: Flow temperature command changes to reflect Kitchen's error (4°C)
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Morning: Bedroom cold (5°C error)
        bedroom_event = create_mock_event("climate.test_bedroom", 16.0, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(bedroom_event))
        
        # Mid-morning: Bedroom warms via heater
        self.mock_time.return_value += 900  # 15 min
        bedroom_event = create_mock_event("climate.test_bedroom", 20.0, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(bedroom_event))
        
        # Now Kitchen becomes coldest (4°C error)
        kitchen_event = create_mock_event("climate.test_kitchen", 17.0, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(kitchen_event))
        
        # Extract final command (should be based on Kitchen's error)
        args = self.mock_hass.services.async_call.call_args[0]
        final_temp = args[2]['value']
        
        expected_kitchen_temp = 40.0 + (4.0 * KP)  # kitchen error 4.0
        self.assertAlmostEqual(final_temp, expected_kitchen_temp, delta=1.0,
                              msg="Command must shift to Kitchen's 4°C error")
    
    def test_sunny_day_gradual_demand_decrease(self):
        """
        Test gradual decrease in boiler demand as temperatures rise over time.
        
        Scenario: Simulate 4 hourly updates with temperature increasing
        from 16°C to 20.5°C (errors: 5, 3.5, 2, 0.5)
        
        Expected: Flow temperature commands decrease monotonically
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        flow_temps = []
        current_temps = [16.0, 17.5, 19.0, 20.5]  # Gradual warming
        
        for i, current_temp in enumerate(current_temps):
            # Reset controller each hour to avoid integral accumulation
            if i > 0:
                controller = MasterController(self.mock_hass, self.zone_configs)
                self.mock_hass.services.async_call.reset_mock()
            
            # Create heating event
            event = create_mock_event("climate.test_bedroom", current_temp, 21.0, 'heating')
            asyncio.run(controller._async_hvac_demand_change(event))
            
            args = self.mock_hass.services.async_call.call_args[0]
            flow_temps.append(args[2]['value'])
            
            self.mock_time.return_value += 3600
        
        # Verify monotonic decrease in flow temperatures
        self.assertGreater(flow_temps[0], flow_temps[-1],
                          "Flow temps must decrease as temperature rises")
        for i in range(len(flow_temps)-1):
            self.assertGreaterEqual(flow_temps[i], flow_temps[i+1],
                                   f"Flow temp should not increase: {flow_temps[i]} -> {flow_temps[i+1]}")


# =========================================================
# RAINY DAY SCENARIO TESTS
# =========================================================

class TestMasterControllerRainyDay(BaseTestFixture):
    """
    Test controller behavior on rainy days with sustained heating demand.
    Simulates minimal solar gain and multiple zone heating patterns.
    """
    
    def test_rainy_day_sustained_heating(self):
        """
        Test sustained boiler operation over extended period on rainy day.
        
        Scenario: Consistent 3°C error across multiple hourly updates
        Expected: Boiler remains active with consistent flow temperature
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        flow_temps = []
        
        # Simulate 4 hours of rainy day: persistent demand
        for hour in range(4):
            event = create_mock_event("climate.test_bedroom", 18.0, 21.0, 'heating')
            asyncio.run(controller._async_hvac_demand_change(event))
            
            args = self.mock_hass.services.async_call.call_args[0]
            flow_temp = args[2]['value']
            flow_temps.append(flow_temp)
            
            self.mock_time.return_value += 3600
        
        # All updates should command active heating
        for temp in flow_temps:
            self.assertGreater(temp, MIN_FLOW_TEMP + 5,
                              "Rainy day: boiler must stay active with significant demand")
    
    def test_rainy_day_multi_zone_equal_demand(self):
        """
        Test load balancing when multiple zones have equal heating demand.
        
        Scenario: Both zones at 17°C, target 21°C (4°C error each)
        Expected: Same flow temperature command regardless of which zone is processed
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Bedroom: 4°C error
        bedroom_event = create_mock_event("climate.test_bedroom", 17.0, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(bedroom_event))
        
        first_call_temp = self.mock_hass.services.async_call.call_args[0][2]['value']
        
        # Kitchen: same 4°C error
        kitchen_event = create_mock_event("climate.test_kitchen", 17.0, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(kitchen_event))
        
        second_call_temp = self.mock_hass.services.async_call.call_args[0][2]['value']
        
        # Should command identical flow temps for equal errors
        self.assertAlmostEqual(first_call_temp, second_call_temp, delta=0.5,
                              msg="Equal zone errors must produce equal flow temps")
    
    def test_rainy_day_one_zone_satisfies_early(self):
        """
        Test system response when one zone reaches target before others.
        
        Scenario:
        - Both zones start at 18°C, target 21°C
        - Bedroom reaches 21°C (satisfied)
        - Kitchen still at 18.5°C (needs heating)
        
        Expected: Flow temperature adjusts to Kitchen's 2.5°C error
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Both zones initially cold
        bedroom_event = create_mock_event("climate.test_bedroom", 18.0, 21.0, 'heating')
        kitchen_event = create_mock_event("climate.test_kitchen", 18.0, 21.0, 'heating')
        
        asyncio.run(controller._async_hvac_demand_change(bedroom_event))
        first_temp = self.mock_hass.services.async_call.call_args[0][2]['value']
        
        # Bedroom reaches target (satisfied)
        self.mock_time.return_value += 1800
        bedroom_satisfied = create_mock_event("climate.test_bedroom", 21.0, 21.0, 'idle')
        asyncio.run(controller._async_hvac_demand_change(bedroom_satisfied))
        
        # Kitchen still needs heating
        kitchen_event = create_mock_event("climate.test_kitchen", 18.5, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(kitchen_event))
        
        second_temp = self.mock_hass.services.async_call.call_args[0][2]['value']
        
        # Verify demand reduced due to Kitchen's smaller error
        self.assertGreater(second_temp, MIN_FLOW_TEMP,
                          "Kitchen's 2.5°C error must still demand heat")
        self.assertLess(second_temp, first_temp,
                       "Second demand (2.5°C) must be less than first (3.0°C)")
    
    def test_rainy_day_slow_temperature_rise(self):
        """
        Test behavior with gradual temperature increase on rainy day.
        Simulates slow, steady heating with minimal solar gain.
        
        Scenario: 5 updates over 50 minutes, each increasing by 0.2°C
        Expected: Decreasing flow temperature as error reduces (5.0 -> 4.2)
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Start with large error
        event = create_mock_event("climate.test_bedroom", 16.0, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(event))
        
        initial_temp = self.mock_hass.services.async_call.call_args[0][2]['value']
        
        # Simulate 5 updates with gradual warming (create fresh controller each time)
        for i in range(5):
            controller = MasterController(self.mock_hass, self.zone_configs)
            self.mock_hass.services.async_call.reset_mock()
            
            self.mock_time.return_value += 600  # 10 minutes
            current = 16.0 + (i * 0.2)  # Increase by 0.2°C each update
            event = create_mock_event("climate.test_bedroom", current, 21.0, 'heating')
            asyncio.run(controller._async_hvac_demand_change(event))
        
        final_temp = self.mock_hass.services.async_call.call_args[0][2]['value']
        
        # Verify decreasing demand (error: 5.0 -> 4.2)
        self.assertLess(final_temp, initial_temp,
                       "Flow temp must decrease with rising temperature")
    
    def test_rainy_day_integral_effect_over_time(self):
        """
        Test integral term accumulation over extended rainy day period.
        
        Scenario: Persistent 2°C error over 2 hours
        Expected: PID integral term (I_sum) accumulates, increasing controller output
        
        Formula: I_sum accumulates error over time -> boosts output for persistent error
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Start with persistent 2°C error
        event1 = create_mock_event("climate.test_bedroom", 19.0, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(event1))
        
        zone = controller.zones["climate.test_bedroom"]
        
        # Simulate 2 hours of persistent error (same zone, time advancing)
        for hour in range(2):
            self.mock_time.return_value += 3600  # +1 hour each iteration
            event = create_mock_event("climate.test_bedroom", 19.0, 21.0, 'heating')
            asyncio.run(controller._async_hvac_demand_change(event))
        
        # Verify integral accumulation
        self.assertGreater(zone.pid_integral_sum, 0,
                          "Integral should accumulate over persistent error")


# =========================================================
# EDGE CASES AND TRANSITIONS
# =========================================================

class TestMasterControllerTransitions(BaseTestFixture):
    """
    Test controller transitions between different scenarios:
    - Sunny to rainy weather change
    - All zones satisfied to sudden demand
    - Demand priority shifts
    """
    
    def test_sunny_to_rainy_transition(self):
        """
        Test system response when sunny day turns rainy.
        
        Scenario:
        - Afternoon sunny: room at 20°C (1°C error)
        - Evening rainy: temperature drops to 19°C (2°C error)
        
        Expected: Boiler demand increases as temperature drops
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Sunny afternoon: minimal error, low demand
        sunny_event = create_mock_event("climate.test_bedroom", 20.0, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(sunny_event))
        sunny_temp = self.mock_hass.services.async_call.call_args[0][2]['value']
        
        # Evening: temperature drops, demand increases
        self.mock_time.return_value += 7200  # 2 hours later
        rainy_event = create_mock_event("climate.test_bedroom", 19.0, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(rainy_event))
        rainy_temp = self.mock_hass.services.async_call.call_args[0][2]['value']
        
        # Verify demand increase
        self.assertLess(sunny_temp, rainy_temp,
                       "Boiler demand must increase as temperature drops")
    
    def test_all_zones_satisfied_to_demanding(self):
        """
        Test boiler behavior when sudden demand appears after all zones satisfied.
        
        Scenario:
        - Initial: Both zones at target (21°C), boiler OFF
        - Sudden: Door opens, cold air influx, demand appears
        
        Expected: Boiler quickly turns ON with significant flow temperature
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # All zones satisfied
        bedroom_event = create_mock_event("climate.test_bedroom", 21.0, 21.0, 'idle')
        kitchen_event = create_mock_event("climate.test_kitchen", 21.0, 21.0, 'idle')
        
        asyncio.run(controller._async_hvac_demand_change(bedroom_event))
        asyncio.run(controller._async_hvac_demand_change(kitchen_event))
        
        # Verify boiler OFF
        off_call = self.mock_hass.services.async_call.call_args[0][2]['value']
        self.assertEqual(off_call, MIN_FLOW_TEMP,
                        "All zones satisfied: boiler should be OFF")
        
        # Sudden demand (door opens)
        self.mock_time.return_value += 1800
        demand_event = create_mock_event("climate.test_bedroom", 18.0, 21.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(demand_event))
        
        on_call = self.mock_hass.services.async_call.call_args[0][2]['value']
        self.assertGreater(on_call, MIN_FLOW_TEMP + 5,
                          "Sudden demand must turn boiler ON with significant output")


if __name__ == '__main__':
    unittest.main()
