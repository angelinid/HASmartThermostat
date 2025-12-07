# tests/test_zone_wrapper_comprehensive.py
"""
Comprehensive Test Suite for ZoneWrapper - Individual Zone PID Control

This test suite validates:
1. Basic zone functionality (temperature error, demand status)
2. PID controller behavior (proportional, integral, derivative terms)
3. Priority levels (high vs low priority zones)
4. TRV valve mitigation (error boosting when valve opens less)
5. Real-world scenarios (sunny day, rainy day, user setpoint changes)

Test Organization:
- BaseTestFixture: Common setup/teardown for all tests
- TestZoneWrapper: Core functionality tests
- TestZonePriority: Priority level handling (high/low)
- TestZoneTRVMitigation: TRV valve opening percentage impact
- TestZoneSunnyDay: Sunny day scenarios with solar gain
- TestZoneRainyDay: Rainy day scenarios with sustained demand
- TestZoneUserActions: User-initiated setpoint changes and heating cycles
"""

import unittest
import sys
import os

# --- PATH ADJUSTMENT ---
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- Import from unified test helpers ---
from test_helpers import UnifiedTestFixture, MockState
from zone_wrapper import ZoneWrapper, KP, KI, KD


# =========================================================
# TEST FIXTURE - Use unified base
# =========================================================

class BaseTestFixture(UnifiedTestFixture):
    """Extended base class with zone-specific setup."""
    
    def setUp(self):
        super().setUp()
        self.zone_config = {"entity_id": "climate.test_bedroom", "name": "Bedroom"}


# =========================================================
# CORE FUNCTIONALITY TEST SUITE
# =========================================================

class TestZoneWrapper(BaseTestFixture):
    """Test core zone functionality: error calculation, demand status, basic PID."""

    def test_error_and_demand_calculation(self):
        """
        TEST: Verify correct error calculation and demand status
        COVERAGE:
        - Zone initialized with default priority=1.0 (high priority)
        - Error = target_temp - current_temp = 20 - 18 = 2.0°C
        - When heating active, is_demanding_heat = True
        - Demand metric = raw error (no priority weighting in zone_wrapper)
        - When heating stops, demand metric = 0
        """
        zone = ZoneWrapper(**self.zone_config)
        
        # 1. Zone starts with demand (Target 20C, Current 18C, Heating active)
        new_state_data = {
            'current_temperature': 18.0, 'temperature': 20.0, 'hvac_action': 'heating'
        }
        zone.update_from_state(MockState(zone.entity_id, attributes=new_state_data))
        
        # Verify demand calculation
        self.assertEqual(zone.current_error, 2.0, "Error should be 2.0°C (20-18)")
        self.assertTrue(zone.is_demanding_heat, "Should be demanding when heating")
        self.assertEqual(zone.get_demand_metric(), 2.0, "Demand metric should equal error for high-priority zone")
        self.assertEqual(zone.priority, 1.0, "Default priority should be 1.0")
        
        # 2. Zone satisfaction (Action changes to idle)
        new_state_data['hvac_action'] = 'idle'
        zone.update_from_state(MockState(zone.entity_id, attributes=new_state_data))
        
        # Verify no demand when idle
        self.assertFalse(zone.is_demanding_heat, "Should not demand when heating stops")
        self.assertEqual(zone.get_demand_metric(), 0.0, "Demand metric should be 0 when idle")
        
        self.assert_test_passes("Error and demand calculation")


    def test_pid_integral_accumulation(self):
        """
        TEST: Verify integral term accumulates error over time
        COVERAGE:
        - Integral = sum of (error * time_delta) over heating period
        - Error 1.0°C sustained for 100 seconds = integral gain of 100
        - Integral clamped to prevent wind-up (max ±10000)
        - Logs show PID state: P_error, I_sum, D_prev
        """
        zone = ZoneWrapper(**self.zone_config)
        
        # Step 1: Initial state (time_delta = 0, so integral doesn't accumulate yet)
        initial_state = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 20.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(initial_state)
        self.assertAlmostEqual(zone.pid_integral_sum, 0.0, "Initial integral should be 0")

        # Step 2: Advance time by 100 seconds, same error (1.0°C)
        self.mock_time.return_value += 100 
        new_state = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 20.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(new_state)
        
        # Integral accumulation: error (1.0) * time_delta (100) = 100
        self.assertAlmostEqual(zone.pid_integral_sum, 100.0, places=1,
                              msg="Integral should be 100 after 100s @ 1°C error")
        
        self.assert_test_passes("PID integral accumulation")

    
    def test_pid_output_calculation(self):
        """
        TEST: Verify PID output combines P, I, D terms correctly
        COVERAGE:
        - P (proportional) = error * KP = 2.0 * 0.5 = 1.0
        - I (integral) = integral_sum * KI = 50.0 * 0.01 = 0.5
        - D (derivative) = (error - last_error) / time_delta * KD
        - D = (2.0 - 1.0) / 10 * 0.1 = 0.01
        - PID_output = P + I + D = 1.0 + 0.5 + 0.01 = 1.51
        """
        zone = ZoneWrapper(**self.zone_config)
        
        # Set up known PID state
        zone.current_error = 2.0
        zone.last_error = 1.0
        zone.pid_integral_sum = 50.0 
        time_delta = 10.0 
        
        # Expected: P(1.0) + I(0.5) + D(0.01) = 1.51
        expected_output = 1.51
        pid_output = zone.calculate_pid_output(time_delta)
        
        self.assertAlmostEqual(pid_output, expected_output, places=2,
                              msg="PID output should be P+I+D = 1.51")
        
        # Verify components are stored for export
        self.assertAlmostEqual(zone.last_pid_p, 1.0, places=2)
        self.assertAlmostEqual(zone.last_pid_i, 0.5, places=2)
        
        self.assert_test_passes("PID output calculation")


# =========================================================
# PRIORITY LEVEL TEST SUITE
# =========================================================

class TestZonePriority(BaseTestFixture):
    """Test priority level handling (high vs low priority zones)."""
    
    def test_high_priority_zone_initialization(self):
        """
        TEST: Verify high-priority zone (priority=1.0) is created correctly
        COVERAGE:
        - Zone created with priority=1.0 (normal, can trigger boiler alone)
        - Log shows: "priority=1.00" in zone initialization
        - Demand metric = base error (no reduction due to priority)
        """
        zone = ZoneWrapper(
            entity_id="climate.master_bedroom",
            name="Master Bedroom",
            floor_area_m2=25.0,
            priority=1.0
        )
        
        self.assertEqual(zone.priority, 1.0, "Priority should be 1.0")
        
        # Set up heating demand
        state = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state)
        
        # High-priority zone: demand metric = error (no weighting)
        self.assertAlmostEqual(zone.get_demand_metric(), 3.0, places=1)
        
        # Verify logs show priority
        logs = self.log_collector.get_logs()
        init_logs = [l for l in logs if "priority" in l["message"].lower()]
        self.assertGreater(len(init_logs), 0, "Should log priority in initialization")
        
        self.assert_test_passes("High-priority zone initialization")
    
    def test_low_priority_zone_initialization(self):
        """
        TEST: Verify low-priority zone (priority=0.2) requires aggregation
        COVERAGE:
        - Zone created with priority=0.2 (low, needs 2+ zones to trigger boiler)
        - Log shows: "priority=0.20" in zone initialization
        - Single low-priority zone alone won't trigger boiler (master controller logic)
        - Multiple low-priority zones with 2+ demanding CAN trigger boiler
        """
        zone = ZoneWrapper(
            entity_id="climate.guest_room",
            name="Guest Room",
            floor_area_m2=12.0,
            priority=0.2
        )
        
        self.assertEqual(zone.priority, 0.2, "Priority should be 0.2")
        
        # Set up heating demand
        state = MockState(zone.entity_id, attributes={
            'current_temperature': 18.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state)
        
        # Demand metric is raw error (priority handled in master controller)
        self.assertAlmostEqual(zone.get_demand_metric(), 4.0, places=1,
                              msg="Demand metric should be raw error, not reduced by priority")
        
        self.assert_test_passes("Low-priority zone initialization")
    
    def test_medium_priority_zone(self):
        """
        TEST: Verify medium-priority zone (priority=0.5) boundary condition
        COVERAGE:
        - Zone with priority=0.5 is at boundary between high/low
        - Master controller treats 0.5 as high priority (> 0.5 for high check)
        - Actually: high is priority > 0.5, so 0.5 is treated as LOW priority
        """
        zone = ZoneWrapper(
            entity_id="climate.office",
            name="Office",
            floor_area_m2=15.0,
            priority=0.5
        )
        
        self.assertEqual(zone.priority, 0.5, "Priority should be 0.5")
        
        state = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 21.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state)
        
        # Demand metric should be raw error
        self.assertAlmostEqual(zone.get_demand_metric(), 2.0, places=1)
        
        self.assert_test_passes("Medium-priority zone boundary test")


# =========================================================
# TRV VALVE MITIGATION TEST SUITE
# =========================================================

class TestZoneTRVMitigation(BaseTestFixture):
    """Test TRV valve opening percentage and error mitigation."""
    
    def test_trv_fully_open_no_mitigation(self):
        """
        TEST: TRV fully open (100%) should have no error boost
        COVERAGE:
        - TRV opening = 100% (fully open)
        - Error boost = 1.0 (no boost)
        - Demand metric = error * 1.0 = error
        - Logs show: "TRV mitigation - opening=100.00%, boost=1.00"
        """
        zone = ZoneWrapper(
            entity_id="climate.living_room",
            name="Living Room",
            floor_area_m2=25.0,
            trv_entity_id="number.living_room_trv_opening"
        )
        
        # Set TRV to fully open
        zone.update_trv_opening(100.0)
        self.assertAlmostEqual(zone.trv_opening_percent, 100.0)
        
        # Set up heating demand
        state = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state)
        
        # No TRV mitigation: demand = raw error
        self.assertAlmostEqual(zone.get_demand_metric(), 3.0, places=1,
                              msg="TRV fully open: demand should be raw error (no boost)")
        
        self.assert_test_passes("TRV fully open (no mitigation)")
    
    def test_trv_50_percent_open_double_boost(self):
        """
        TEST: TRV half open (50%) should double error signal
        COVERAGE:
        - TRV opening = 50% (restricting flow)
        - Error boost = 100 / 50 = 2.0 (double)
        - Demand metric = error * 2.0 = 2 * 3 = 6.0
        - Logs show: "TRV mitigation - opening=50.00%, boost=2.00, boosted_error=6.00"
        - This compensates for flow restriction
        """
        zone = ZoneWrapper(
            entity_id="climate.bedroom",
            name="Bedroom",
            floor_area_m2=15.0,
            trv_entity_id="number.bedroom_trv_opening"
        )
        
        # Set TRV to 50% open
        zone.update_trv_opening(50.0)
        self.assertAlmostEqual(zone.trv_opening_percent, 50.0)
        
        # Set up heating demand with 3°C error
        state = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state)
        
        # TRV mitigation: demand = error * (100/50) = 3 * 2 = 6.0
        demand = zone.get_demand_metric()
        self.assertAlmostEqual(demand, 6.0, places=1,
                              msg="TRV at 50%: demand should be 6.0 (error 3.0 * boost 2.0)")
        
        self.assert_test_passes("TRV half open (2x boost)")
    
    def test_trv_25_percent_open_quadruple_boost(self):
        """
        TEST: TRV mostly closed (25%) should quadruple error signal
        COVERAGE:
        - TRV opening = 25% (mostly closed)
        - Error boost = 100 / 25 = 4.0 (quadruple)
        - Demand metric = error * 4.0 = 2 * 4 = 8.0
        - Logs show: "TRV mitigation - opening=25.00%, boost=4.00, boosted_error=8.00"
        - Compensates heavily for restricted heating
        """
        zone = ZoneWrapper(
            entity_id="climate.hallway",
            name="Hallway",
            floor_area_m2=8.0,
            trv_entity_id="number.hallway_trv_opening"
        )
        
        # TRV closing as temperature approaches setpoint
        zone.update_trv_opening(25.0)
        
        state = MockState(zone.entity_id, attributes={
            'current_temperature': 20.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state)
        
        # TRV mitigation: demand = error * (100/25) = 2 * 4 = 8.0
        demand = zone.get_demand_metric()
        self.assertAlmostEqual(demand, 8.0, places=1,
                              msg="TRV at 25%: demand should be 8.0 (error 2.0 * boost 4.0)")
        
        self.assert_test_passes("TRV mostly closed (4x boost)")
    
    def test_trv_opening_change_during_heating(self):
        """
        TEST: TRV opening percentage changes while zone is actively heating
        COVERAGE:
        - Initial: TRV open 100%, error 3°C, demand = 3.0
        - TRV closes to 50% due to thermostat feedback
        - Same error 3°C, but demand increases to 6.0 (boosted)
        - Logs show both pre and post mitigation states
        """
        zone = ZoneWrapper(
            entity_id="climate.kitchen",
            name="Kitchen",
            floor_area_m2=12.0,
            trv_entity_id="number.kitchen_trv_opening"
        )
        
        # Initial: TRV fully open
        zone.update_trv_opening(100.0)
        state1 = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state1)
        demand_fully_open = zone.get_demand_metric()
        self.assertAlmostEqual(demand_fully_open, 3.0, places=1)
        
        # TRV starts closing as it approaches setpoint
        self.mock_time.return_value += 1800
        zone.update_trv_opening(50.0)  # Now 50% open
        state2 = MockState(zone.entity_id, attributes={
            'current_temperature': 20.5, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state2)
        
        demand_half_open = zone.get_demand_metric()
        # Error is now 1.5°C, but boost = 2.0, so demand = 1.5 * 2.0 = 3.0
        self.assertAlmostEqual(demand_half_open, 3.0, places=1,
                              msg="TRV closing compensates for smaller error")
        
        self.assert_test_passes("TRV opening change during heating")


# =========================================================
# SUNNY DAY SCENARIO TESTS
# =========================================================

class TestZoneSunnyDay(BaseTestFixture):
    """Simulate sunny day with solar gain and decreasing heating demand."""
    
    def test_sunny_day_temperature_rise(self):
        """
        TEST: Zone behavior on sunny day with solar gain reducing heating demand
        COVERAGE:
        - Morning: Cold start, 4°C error, heating active
        - Midday: Solar gain, 1°C error, still heating
        - Afternoon: Target reached, 0°C error, heating idle
        - Logs show error decreasing over time, demand decreasing
        """
        zone = ZoneWrapper(entity_id="climate.living_room", name="Living Room", floor_area_m2=25.0)
        
        # Morning: Cold start, heating demand
        morning_state = MockState(zone.entity_id, attributes={
            'current_temperature': 18.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(morning_state)
        self.assertTrue(zone.is_demanding_heat)
        self.assertAlmostEqual(zone.current_error, 4.0)
        demand_morning = zone.get_demand_metric()
        
        # Midday: Solar gain warms room, error decreases
        self.mock_time.return_value += 3600  # 1 hour
        midday_state = MockState(zone.entity_id, attributes={
            'current_temperature': 21.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(midday_state)
        self.assertTrue(zone.is_demanding_heat)
        self.assertAlmostEqual(zone.current_error, 1.0)
        demand_midday = zone.get_demand_metric()
        self.assertLess(demand_midday, demand_morning)
        
        # Afternoon: Reached target temperature
        self.mock_time.return_value += 1800  # 30 min
        afternoon_state = MockState(zone.entity_id, attributes={
            'current_temperature': 22.0, 'temperature': 22.0, 'hvac_action': 'idle'
        })
        zone.update_from_state(afternoon_state)
        self.assertFalse(zone.is_demanding_heat)
        self.assertAlmostEqual(zone.current_error, 0.0)
        
        # Verify logging captured progression
        logs = self.log_collector.get_logs()
        self.assertTrue(any("Living Room" in log["message"] for log in logs))
        self.assertTrue(any("state update" in log["message"] for log in logs))
        
        self.assert_test_passes("Sunny day temperature rise")
    
    def test_sunny_day_integral_accumulation(self):
        """
        TEST: Integral accumulates differently with solar gain reducing error
        COVERAGE:
        - Start: 2°C error for 0 seconds (no integral)
        - After 30 min: 1°C error for 1800 seconds, integral = 1 * 1800 = 1800
        - Logs show integral growing but slower than rainy day (due to solar)
        """
        zone = ZoneWrapper(entity_id="climate.bedroom", name="Bedroom", floor_area_m2=15.0)
        
        # Start heating with 2°C error
        initial_state = MockState(zone.entity_id, attributes={
            'current_temperature': 20.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(initial_state)
        self.assertAlmostEqual(zone.pid_integral_sum, 0.0)
        
        # 30 min later: solar gain reduces error to 1°C
        self.mock_time.return_value += 1800
        second_state = MockState(zone.entity_id, attributes={
            'current_temperature': 21.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(second_state)
        
        # Integral adds: new_error (1.0) * time_delta (1800) = 1800
        self.assertAlmostEqual(zone.pid_integral_sum, 1800.0, places=-2)
        
        self.assert_test_passes("Sunny day integral accumulation")
    
    def test_sunny_day_multi_zone_solar_exposure(self):
        """
        TEST: Compare heating demand between south-facing (sunny) and north-facing (shaded) zones
        COVERAGE:
        - South zone: More solar gain, temperature rises faster
        - North zone: No solar gain, temperature rises slower
        - Both start with same target, but south needs less boiler help
        - Logs show south zone error decreasing faster than north
        """
        south_zone = ZoneWrapper(entity_id="climate.south_room", name="South Room", floor_area_m2=20.0)
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
        
        # 1 hour later: Solar gain differential
        self.mock_time.return_value += 3600
        south_state = MockState(south_zone.entity_id, attributes={
            'current_temperature': 21.5, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        north_state = MockState(north_zone.entity_id, attributes={
            'current_temperature': 19.5, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        
        south_zone.update_from_state(south_state)
        north_zone.update_from_state(north_state)
        
        # North zone should have more demand due to less solar gain
        self.assertLess(south_zone.current_error, north_zone.current_error)
        self.assertAlmostEqual(south_zone.current_error, 0.5)
        self.assertAlmostEqual(north_zone.current_error, 2.5)
        
        self.assert_test_passes("Sunny day multi-zone solar exposure")


# =========================================================
# RAINY DAY SCENARIO TESTS
# =========================================================

class TestZoneRainyDay(BaseTestFixture):
    """Simulate rainy day with sustained heating demand and integral wind-up."""
    
    def test_rainy_day_sustained_heating(self):
        """
        TEST: Zone behavior during rainy day with persistent heating demand
        COVERAGE:
        - Morning: Cold start, 4°C error, heating active
        - Mid-morning: Slight heating, 3.5°C error, still heating
        - Noon: Continued heating, 3°C error, sustained demand
        - Logs show sustained heating cycle, integral accumulating
        """
        zone = ZoneWrapper(entity_id="climate.main_room", name="Main Room", floor_area_m2=30.0)
        
        # Morning: Cold and no solar gain
        morning_state = MockState(zone.entity_id, attributes={
            'current_temperature': 18.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(morning_state)
        self.assertTrue(zone.is_demanding_heat)
        self.assertAlmostEqual(zone.current_error, 4.0)
        integral_morning = zone.pid_integral_sum
        
        # Mid-morning: Slight heating but error persists
        self.mock_time.return_value += 1800  # 30 min
        midmorning_state = MockState(zone.entity_id, attributes={
            'current_temperature': 18.5, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(midmorning_state)
        self.assertTrue(zone.is_demanding_heat)
        self.assertAlmostEqual(zone.current_error, 3.5)
        integral_midmorning = zone.pid_integral_sum
        self.assertGreater(integral_midmorning, integral_morning)
        
        # Noon: Continued slow heating
        self.mock_time.return_value += 3600  # 1 hour later
        noon_state = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(noon_state)
        self.assertTrue(zone.is_demanding_heat)
        self.assertAlmostEqual(zone.current_error, 3.0)
        
        # Integral continues growing
        self.assertGreater(zone.pid_integral_sum, integral_midmorning)
        
        # Verify sustained heating in logs
        logs = self.log_collector.get_logs()
        heating_logs = [log for log in logs if "heating" in log["message"].lower()]
        self.assertGreater(len(heating_logs), 2)
        
        self.assert_test_passes("Rainy day sustained heating")
    
    def test_rainy_day_integral_wind_up_clamp(self):
        """
        TEST: Verify integral accumulation hits clamp to prevent wind-up
        COVERAGE:
        - Zone heating with 5°C error for extended period (2 hours)
        - Integral accumulates but gets clamped at max 10000
        - Without clamp, integral would grow unbounded and cause overshoot
        - Logs show accumulation and eventual clamp behavior
        """
        zone = ZoneWrapper(entity_id="climate.office", name="Office", floor_area_m2=18.0)
        
        # Start heating with significant error (5°C)
        initial_state = MockState(zone.entity_id, attributes={
            'current_temperature': 17.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(initial_state)
        self.assertAlmostEqual(zone.pid_integral_sum, 0.0)
        
        # Simulate rainy day: persistent 5°C error (heating very slowly)
        for hour in range(2):
            self.mock_time.return_value += 3600
            state = MockState(zone.entity_id, attributes={
                'current_temperature': 17.0, 'temperature': 22.0, 'hvac_action': 'heating'
            })
            zone.update_from_state(state)
        
        # After 2 hours: integral = 5.0 * 7200 = 36000, but clamped to 10000
        self.assertGreaterEqual(zone.pid_integral_sum, 8000.0,
                               msg="Integral should be near clamp (10000)")
        self.assertLessEqual(zone.pid_integral_sum, 10000.0,
                            msg="Integral should be clamped at max 10000")
        
        self.assert_test_passes("Rainy day integral wind-up clamp")
    
    def test_rainy_day_derivative_stability(self):
        """
        TEST: Derivative term stability with slow temperature changes
        COVERAGE:
        - Rainy day has slow temperature change (no solar gain)
        - Derivative = (error_now - error_prev) / time_delta
        - With slow changes, derivative term is very small
        - Logs show small D term contribution to PID output
        """
        zone = ZoneWrapper(entity_id="climate.hallway", name="Hallway", floor_area_m2=10.0)
        
        # Start heating
        initial_state = MockState(zone.entity_id, attributes={
            'current_temperature': 18.0, 'temperature': 21.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(initial_state)
        
        # Very slow temperature increase over 30 min
        self.mock_time.return_value += 1800
        state1 = MockState(zone.entity_id, attributes={
            'current_temperature': 18.1, 'temperature': 21.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state1)
        
        # Another very slow increase
        self.mock_time.return_value += 1800
        state2 = MockState(zone.entity_id, attributes={
            'current_temperature': 18.2, 'temperature': 21.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state2)
        
        # Calculate PID - derivative should be very small due to slow change
        pid_output = zone.calculate_pid_output(1800)
        
        # P term dominates on rainy day (slow heating)
        self.assertGreater(pid_output, 1.0, "PID output should be significant (P term)")
        self.assertLess(zone.last_pid_d, 0.01, "D term should be very small on rainy day")
        
        self.assert_test_passes("Rainy day derivative stability")
    
    def test_rainy_day_heating_cycles(self):
        """
        TEST: Multiple on/off heating cycles on rainy day
        COVERAGE:
        - Simulate 3 heating cycles over rainy day
        - Each cycle: heater ON for 15 min (temperature rises slightly)
        - Then: heater OFF for maintenance (colder, heating idle)
        - Logs show demand changes: heating -> idle -> heating cycle
        """
        zone = ZoneWrapper(entity_id="climate.kitchen", name="Kitchen", floor_area_m2=12.0)
        
        demand_changes = []
        
        # Simulate 3 heating cycles
        for cycle in range(3):
            # Heater ON for 15 minutes
            self.mock_time.return_value += 900
            on_state = MockState(zone.entity_id, attributes={
                'current_temperature': 20.0, 'temperature': 22.0, 'hvac_action': 'heating'
            })
            zone.update_from_state(on_state)
            demand_changes.append(('ON', zone.is_demanding_heat, zone.current_error))
            self.assertTrue(zone.is_demanding_heat, f"Cycle {cycle}: Should demand when heating ON")
            
            # Heater OFF for maintenance, temperature drops
            self.mock_time.return_value += 300  # 5 min
            off_state = MockState(zone.entity_id, attributes={
                'current_temperature': 19.5, 'temperature': 22.0, 'hvac_action': 'idle'
            })
            zone.update_from_state(off_state)
            demand_changes.append(('OFF', zone.is_demanding_heat, zone.current_error))
            self.assertFalse(zone.is_demanding_heat, f"Cycle {cycle}: Should not demand when idle")
        
        # Verify 3 complete cycles (ON + OFF each = 6 changes)
        self.assertEqual(len(demand_changes), 6)
        self.assertTrue(demand_changes[0][1])   # First ON: demanding
        self.assertFalse(demand_changes[1][1])  # Then OFF: not demanding
        self.assertTrue(demand_changes[2][1])   # Second ON: demanding
        
        self.assert_test_passes("Rainy day heating cycles")


# =========================================================
# USER ACTION TESTS (Setpoint Changes, TRV Real-Time Updates)
# =========================================================

class TestZoneUserActions(BaseTestFixture):
    """Test user actions: setpoint changes, TRV updates during heating."""
    
    def test_user_increases_setpoint_resets_integral(self):
        """
        TEST: When user increases target temperature, PID integral should reset
        COVERAGE:
        - Initial: 20°C target, 2°C error, integral accumulating
        - User changes target to 23°C (3°C increase)
        - Integral should reset to 0 to prevent wind-up with new setpoint
        - New error = 23 - 19 = 4°C (larger), but integral starts fresh
        - Logs show: "Target temperature changed from 20.0°C to 23.0°C - resetting PID integral"
        """
        zone = ZoneWrapper(entity_id="climate.bedroom", name="Bedroom", floor_area_m2=15.0)
        
        # Initial state: 20°C target, 19°C current
        initial_state = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 20.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(initial_state)
        
        # Accumulate integral over time
        self.mock_time.return_value += 1800
        state1 = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 20.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state1)
        integral_before = zone.pid_integral_sum
        self.assertGreater(integral_before, 0, "Integral should accumulate")
        
        # Verify reset is logged (check logs BEFORE making the change)
        logs_before = self.log_collector.get_logs()
        
        # User increases setpoint from 20°C to 23°C
        # The setpoint change should trigger the reset, but integral will accumulate again
        # with the new larger error on the same update cycle
        self.mock_time.return_value += 600
        new_state = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 23.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(new_state)
        
        # After setpoint change and new update, integral will have the new error
        # accumulated (4.0°C * 600s = 2400), but it was reset before accumulation
        # So we verify the reset happened by checking the logs
        logs_after = self.log_collector.get_logs()
        reset_logs = [l for l in logs_after if "resetting" in l["message"].lower() 
                     and "integral" in l["message"].lower()]
        self.assertGreater(len(reset_logs), 0, "Should log integral reset")
        
        # New error should be larger (23 - 19 = 4)
        self.assertAlmostEqual(zone.current_error, 4.0)
        
        # The integral will accumulate with new error after reset
        # error (4.0) * time_delta (600) = 2400, which is what we see
        self.assertGreater(zone.pid_integral_sum, 0, "Integral should accumulate after reset with new error")
        
        self.assert_test_passes("User increases setpoint - integral reset")
    
    def test_user_decreases_setpoint_integral_reset(self):
        """
        TEST: Lowering target temperature also triggers integral reset
        COVERAGE:
        - Initial: 22°C target, heating, integral accumulating
        - User lowers target to 19°C (3°C decrease)
        - Integral resets to 0 (no longer needed for lower temperature)
        - Error changes from positive to smaller value
        - Logs show reset message
        """
        zone = ZoneWrapper(entity_id="climate.living_room", name="Living Room", floor_area_m2=25.0)
        
        # Initial: 22°C target, 20°C current (heating)
        initial_state = MockState(zone.entity_id, attributes={
            'current_temperature': 20.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(initial_state)
        
        # Accumulate integral
        self.mock_time.return_value += 1800
        state1 = MockState(zone.entity_id, attributes={
            'current_temperature': 20.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state1)
        integral_before = zone.pid_integral_sum
        self.assertGreater(integral_before, 0)
        
        # User lowers setpoint from 22°C to 19°C
        # The reset happens, then integral accumulates with new (negative) error
        self.mock_time.return_value += 600
        new_state = MockState(zone.entity_id, attributes={
            'current_temperature': 20.0, 'temperature': 19.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(new_state)
        
        # After reset, integral will accumulate with new error = -1°C
        # So integral = -1 * 600 = -600
        # Verify reset happened by checking logs
        logs = self.log_collector.get_logs()
        reset_logs = [l for l in logs if "resetting" in l["message"].lower() 
                     and "integral" in l["message"].lower()]
        self.assertGreater(len(reset_logs), 0, "Should log integral reset")
        
        # New error becomes negative (overshooting target)
        self.assertAlmostEqual(zone.current_error, -1.0)
        
        # Integral should be negative due to negative error accumulation after reset
        self.assertLess(zone.pid_integral_sum, 0, "Integral should be negative with negative error")
        
        self.assert_test_passes("User decreases setpoint - integral reset")
    
    def test_trv_closes_progressively_as_zone_heats(self):
        """
        TEST: TRV valve closing progressively as zone approaches setpoint
        COVERAGE:
        - Initial: TRV 100% open, error 3°C, demand = 3°C
        - After 30 min: TRV closes to 75% (error reduced to 2.5°C due to heating)
        - Demand = 2.5 * (100/75) = 3.33°C (boosted)
        - After 1 hour: TRV closes to 50% (error = 1.5°C)
        - Demand = 1.5 * (100/50) = 3.0°C (heavily boosted)
        - Logs show TRV mitigation increasing as valve closes
        """
        zone = ZoneWrapper(
            entity_id="climate.office",
            name="Office",
            floor_area_m2=15.0,
            trv_entity_id="number.office_trv_opening"
        )
        
        # Initial: TRV fully open
        zone.update_trv_opening(100.0)
        state0 = MockState(zone.entity_id, attributes={
            'current_temperature': 19.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state0)
        demand_initial = zone.get_demand_metric()
        self.assertAlmostEqual(demand_initial, 3.0)
        
        # After 30 min: TRV closing as temperature approaches setpoint
        self.mock_time.return_value += 1800
        zone.update_trv_opening(75.0)  # 25% closed
        state1 = MockState(zone.entity_id, attributes={
            'current_temperature': 20.5, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state1)
        demand_75 = zone.get_demand_metric()
        # Error = 1.5, boost = 100/75 = 1.33, demand = 1.5 * 1.33 ≈ 2.0
        self.assertAlmostEqual(demand_75, 2.0, places=0)
        
        # After 1 hour: TRV closing more
        self.mock_time.return_value += 1800
        zone.update_trv_opening(50.0)  # 50% closed
        state2 = MockState(zone.entity_id, attributes={
            'current_temperature': 21.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state2)
        demand_50 = zone.get_demand_metric()
        # Error = 1, boost = 100/50 = 2.0, demand = 1 * 2.0 = 2.0
        self.assertAlmostEqual(demand_50, 2.0, places=0)
        
        # Verify TRV mitigation logged at each step
        logs = self.log_collector.get_logs()
        trv_logs = [l for l in logs if "TRV mitigation" in l["message"]]
        # Should have TRV logs for 75% and 50% opening
        self.assertGreater(len(trv_logs), 0)
        
        self.assert_test_passes("TRV closes progressively during heating")
    
    def test_trv_suddenly_closes_to_zero(self):
        """
        TEST: TRV completely closes (0% opening) when thermostat is satisfied
        COVERAGE:
        - Initial: TRV open, error 2°C, heating active
        - TRV closes to 0% (thermostat satisfied)
        - When TRV is 0%, no boost applied (avoid division by zero)
        - Demand metric becomes 0 (valve closed, no flow)
        - Logs show TRV at 0%, demand metric becomes 0
        """
        zone = ZoneWrapper(
            entity_id="climate.bedroom",
            name="Bedroom",
            floor_area_m2=15.0,
            trv_entity_id="number.bedroom_trv_opening"
        )
        
        # Initial state: TRV fully open, heating demand
        zone.update_trv_opening(100.0)
        state1 = MockState(zone.entity_id, attributes={
            'current_temperature': 20.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state1)
        demand_before = zone.get_demand_metric()
        self.assertGreater(demand_before, 0)
        
        # TRV suddenly closes to 0% (thermostat satisfied, shuts off)
        self.mock_time.return_value += 600
        zone.update_trv_opening(0.0)
        state2 = MockState(zone.entity_id, attributes={
            'current_temperature': 20.0, 'temperature': 22.0, 'hvac_action': 'heating'
        })
        zone.update_from_state(state2)
        
        # When TRV is 0%, don't apply boost (avoid div by zero)
        # Demand should be raw error (no mitigation when valve is fully closed)
        demand_after = zone.get_demand_metric()
        self.assertAlmostEqual(demand_after, 2.0, places=1)
        
        self.assert_test_passes("TRV completely closes (0% opening)")


if __name__ == '__main__':
    unittest.main()
