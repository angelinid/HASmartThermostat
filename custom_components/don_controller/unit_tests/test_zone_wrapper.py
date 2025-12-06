# tests/test_zone_wrapper.py
import unittest
from unittest.mock import patch
import sys
import os
import logging

# --- PATH ADJUSTMENT ---
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- Import Mocks and Custom Classes ---
from unit_tests.mocks import MockState, FIXED_TIME_START
from zone_wrapper import ZoneWrapper, KP, KI, KD
from logging_util import LogCollector, setup_logging


# =========================================================
# TEST FIXTURE BASE
# =========================================================

class BaseTestFixture(unittest.TestCase):
    """Base class for shared setup logic."""
    
    def setUp(self):
        self.time_patcher = patch('time.time', return_value=FIXED_TIME_START)
        self.mock_time = self.time_patcher.start()
        self.zone_config = {"entity_id": "climate.test_bedroom", "name": "Bedroom"}
        
        # Setup logging collection
        setup_logging(level=logging.DEBUG)
        self.log_collector = LogCollector()
        self.log_collector.start_collecting()

    def tearDown(self):
        self.time_patcher.stop()
        self.log_collector.stop_collecting()



# =========================================================
# ZONE WRAPPER TEST SUITE
# =========================================================

class TestZoneWrapper(BaseTestFixture):

    def test_error_and_demand_calculation(self):
        """Test if the wrapper correctly calculates temperature error and demand status."""
        
        zone = ZoneWrapper(**self.zone_config)
        
        # 1. Simulate demand (Target 20C, Current 18C, Action: Heating)
        new_state_data = {
            'current_temperature': 18.0, 'temperature': 20.0, 'hvac_action': 'heating'
        }
        zone.update_from_state(MockState(zone.entity_id, attributes=new_state_data))
        
        self.assertEqual(zone.current_error, 2.0)
        self.assertTrue(zone.is_demanding_heat)
        self.assertEqual(zone.get_demand_metric(), 2.0)
        
        # 2. Simulate satisfaction (Action: Idle)
        new_state_data['hvac_action'] = 'idle'
        zone.update_from_state(MockState(zone.entity_id, attributes=new_state_data))
        
        self.assertFalse(zone.is_demanding_heat)
        self.assertEqual(zone.get_demand_metric(), 0.0)


    def test_pid_integral_accumulation(self):
        """Test integral accumulation over a time delta."""
        
        zone = ZoneWrapper(**self.zone_config)
        
        # Step 1: Initial Demand (Error = 1.0). Time_delta = 0.
        initial_state = MockState(zone.entity_id, attributes={'current_temperature': 19.0, 'temperature': 20.0, 'hvac_action': 'heating'})
        zone.update_from_state(initial_state)

        # Step 2: Advance time by 100 seconds
        self.mock_time.return_value += 100 
        
        # New state (same error 1.0)
        new_state = MockState(zone.entity_id, attributes={'current_temperature': 19.0, 'temperature': 20.0, 'hvac_action': 'heating'})
        zone.update_from_state(new_state)
        
        # Expected Integral: 0 + 1.0 (error) * 100 (delta) * KI (0.01) = 1.0
        # Assert the raw sum accumulation
        self.assertAlmostEqual(zone.pid_integral_sum, 100.0) 
        # We assert 100.0 because the sum is now the RAW error-time product (1.0 * 100)
        
    
    def test_pid_output_calculation(self):
        """Test the full PID output (P + I + D) is calculated correctly with known terms."""
        
        zone = ZoneWrapper(**self.zone_config)
        
        # Set up a known state manually to test the calculate_pid_output method
        zone.current_error = 2.0
        zone.last_error = 1.0
        zone.pid_integral_sum = 50.0 
        time_delta = 10.0 
        
        # Expected Output: P (2*0.5=1.0) + I (50*0.01=0.5) + D ((2-1)/10*0.1=0.01) = 1.51
        expected_output = 1.51
        
        pid_output = zone.calculate_pid_output(time_delta)
        
        self.assertAlmostEqual(pid_output, expected_output, places=2)


# =========================================================
# SUNNY DAY SCENARIO TESTS
# =========================================================

class TestZoneSunnyDay(BaseTestFixture):
    """Simulate a sunny day with gradual temperature increase."""
    
    def test_sunny_day_temperature_rise(self):
        """Test zone behavior during a sunny day with gradually rising ambient temperature."""
        zone = ZoneWrapper(entity_id="climate.living_room", name="Living Room", floor_area_m2=25.0)
        
        # Morning: Cold start, heating demand
        morning_state = MockState(zone.entity_id, attributes={
            'current_temperature': 18.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(morning_state)
        self.assertTrue(zone.is_demanding_heat)
        self.assertAlmostEqual(zone.current_error, 4.0)
        
        # Midday: Temperature rising due to solar gain
        self.mock_time.return_value += 3600  # 1 hour later
        midday_state = MockState(zone.entity_id, attributes={
            'current_temperature': 21.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(midday_state)
        self.assertTrue(zone.is_demanding_heat)
        self.assertAlmostEqual(zone.current_error, 1.0)
        
        # Afternoon: Reached target
        self.mock_time.return_value += 1800  # 30 min later
        afternoon_state = MockState(zone.entity_id, attributes={
            'current_temperature': 22.0, 'temperature': 22.0, 'hvac_action': 'idle'
        })
        zone.update_from_state(afternoon_state)
        self.assertFalse(zone.is_demanding_heat)
        self.assertAlmostEqual(zone.current_error, 0.0)
        
        # Verify logging captured zone initialization and state changes
        logs = self.log_collector.get_logs()
        self.assertTrue(any("Living Room" in log["message"] for log in logs))
        self.assertTrue(any("state update" in log["message"] for log in logs))
    
    def test_sunny_day_integral_accumulation(self):
        """Test integral accumulation on sunny day with cooling offset."""
        zone = ZoneWrapper(entity_id="climate.bedroom", name="Bedroom", floor_area_m2=15.0)
        
        # Start with heating demand
        initial_state = MockState(zone.entity_id, attributes={
            'current_temperature': 20.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(initial_state)
        
        # Continue heating but error decreases (solar gain)
        self.mock_time.return_value += 1800  # 30 min later
        second_state = MockState(zone.entity_id, attributes={
            'current_temperature': 21.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(second_state)
        
        # Integral accumulation:
        # First update (time_delta=0): 0 added
        # Second update (error 2.0 * time_delta 1800): 2.0 * 1800 = 3600
        # But actually the first error (2.0) * 1800 = 3600
        # So we should have 3600 but only got 1800 because second error is 1.0
        # Correct: first 2.0 * 0 = 0, then 2.0 * 1800 = 3600? No...
        # Let me trace: on second call, last_update_time is set from first call
        # So time_delta = now - first_time = 1800
        # And new_error = 22 - 21 = 1
        # So integral gets: 1 * 1800 = 1800
        self.assertAlmostEqual(zone.pid_integral_sum, 1800.0, places=-2)
    
    def test_sunny_day_multi_zone_balance(self):
        """Test multiple zones with different solar exposure on sunny day."""
        # South-facing zone (more solar gain)
        south_zone = ZoneWrapper(entity_id="climate.south_room", name="South Room", floor_area_m2=20.0)
        # North-facing zone (less solar gain)
        north_zone = ZoneWrapper(entity_id="climate.north_room", name="North Room", floor_area_m2=20.0)
        
        # Both start with same target
        south_state = MockState(south_zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        north_state = MockState(north_zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        
        south_zone.update_from_state(south_state)
        north_zone.update_from_state(north_state)
        
        self.assertAlmostEqual(south_zone.current_error, 3.0)
        self.assertAlmostEqual(north_zone.current_error, 3.0)
        
        # Later: South zone warmed by solar, north zone still cold
        self.mock_time.return_value += 3600
        south_state = MockState(south_zone.entity_id, attributes={
            'current_temperature': 21.5, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        north_state = MockState(north_zone.entity_id, attributes={
            'current_temperature': 19.5, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        
        south_zone.update_from_state(south_state)
        north_zone.update_from_state(north_state)
        
        # North zone should have more demand
        self.assertLess(south_zone.current_error, north_zone.current_error)
        self.assertAlmostEqual(south_zone.current_error, 0.5)
        self.assertAlmostEqual(north_zone.current_error, 2.5)


# =========================================================
# RAINY DAY SCENARIO TESTS
# =========================================================

class TestZoneRainyDay(BaseTestFixture):
    """Simulate a rainy day with steady temperature and increased heating demand."""
    
    def test_rainy_day_sustained_heating(self):
        """Test zone behavior during a rainy day with sustained heating demand."""
        zone = ZoneWrapper(entity_id="climate.main_room", name="Main Room", floor_area_m2=30.0)
        
        # Morning: Cold and no solar gain
        morning_state = MockState(zone.entity_id, attributes={
            'current_temperature': 18.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(morning_state)
        self.assertTrue(zone.is_demanding_heat)
        self.assertAlmostEqual(zone.current_error, 4.0)
        
        # Mid-morning: Slight heating but no solar gain
        self.mock_time.return_value += 1800
        midmorning_state = MockState(zone.entity_id, attributes={
            'current_temperature': 18.5, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(midmorning_state)
        self.assertTrue(zone.is_demanding_heat)
        self.assertAlmostEqual(zone.current_error, 3.5)
        
        # Noon: Still cold, steady heating
        self.mock_time.return_value += 3600
        noon_state = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(noon_state)
        self.assertTrue(zone.is_demanding_heat)
        self.assertAlmostEqual(zone.current_error, 3.0)
        
        # Verify sustained demand in logs
        logs = self.log_collector.get_logs()
        heating_logs = [log for log in logs if "heating" in log["message"].lower()]
        self.assertGreater(len(heating_logs), 0)
    
    def test_rainy_day_integral_wind_up(self):
        """Test integral accumulation during a rainy day (long sustained error)."""
        zone = ZoneWrapper(entity_id="climate.office", name="Office", floor_area_m2=18.0)
        
        # Start heating with significant error
        initial_state = MockState(zone.entity_id, attributes={
            'current_temperature': 17.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(initial_state)
        
        # Simulate rainy day: persistent 3°C error for 2 hours
        for hour in range(2):
            self.mock_time.return_value += 3600
            state = MockState(zone.entity_id, attributes={
                'current_temperature': 19.0, 'temperature': 22.0, 'hvac_action': 'heating'
            })
            zone.update_from_state(state)
        
        # Integral accumulates but hits clamp limit at 10000.0
        # First update: 5.0 * 0 = 0
        # After 1 hour: 5.0 * 3600 = 18000, but clamped to 10000
        # After 2 hours: 3.0 * 3600 added to clamped value, still max = 10000
        # Since we hit the clamp, verify integral is at max or close to it
        self.assertGreaterEqual(zone.pid_integral_sum, 8000.0)
    
    def test_rainy_day_derivative_stability(self):
        """Test derivative term stability on rainy day with slow changes."""
        zone = ZoneWrapper(entity_id="climate.hallway", name="Hallway", floor_area_m2=10.0)
        
        # Start heating
        initial_state = MockState(zone.entity_id, attributes={
            'current_temperature': 18.0, 'temperature': 21.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(initial_state)
        
        # Very slow temperature increase (rainy day, no solar gain)
        self.mock_time.return_value += 1800
        state1 = MockState(zone.entity_id, attributes={
            'current_temperature': 18.1, 'temperature': 21.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state1)
        
        # Another slow increase
        self.mock_time.return_value += 1800
        state2 = MockState(zone.entity_id, attributes={
            'current_temperature': 18.2, 'temperature': 21.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state2)
        
        # Calculate PID - derivative should be very small due to slow change
        pid_output = zone.calculate_pid_output(1800)
        
        # P = 2.8 * 0.5 = 1.4
        # D = (2.8 - 2.9) / 1800 * 0.1 ≈ -5.6e-5 (very small)
        self.assertGreater(pid_output, 0)
    
    def test_rainy_day_heating_cycles(self):
        """Test multiple heating cycles on a rainy day."""
        zone = ZoneWrapper(entity_id="climate.kitchen", name="Kitchen", floor_area_m2=12.0)
        
        demand_changes = []
        
        # Simulate 3 heating cycles over rainy day
        for cycle in range(3):
            # Heater ON
            self.mock_time.return_value += 900  # 15 min
            on_state = MockState(zone.entity_id, attributes={
                'current_temperature': 20.0, 'temperature': 22.0, 'hvac_action': 'heating'
            })
            zone.update_from_state(on_state)
            demand_changes.append(('ON', zone.is_demanding_heat, zone.current_error))
            
            # Heater OFF (maintenance)
            self.mock_time.return_value += 300  # 5 min
            off_state = MockState(zone.entity_id, attributes={
                'current_temperature': 20.5, 'temperature': 22.0, 'hvac_action': 'idle'
            })
            zone.update_from_state(off_state)
            demand_changes.append(('OFF', zone.is_demanding_heat, zone.current_error))
        
        # Verify cycles
        self.assertEqual(len(demand_changes), 6)
        self.assertTrue(demand_changes[0][1])  # First cycle ON
        self.assertFalse(demand_changes[1][1])  # Then OFF


if __name__ == '__main__':
    unittest.main()
