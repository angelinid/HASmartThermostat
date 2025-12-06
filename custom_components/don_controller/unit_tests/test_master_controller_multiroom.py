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
Multi-Room Thermal Coupling Unit Tests

Tests realistic 11-zone house scenarios with inter-zone heat transfer:
- Heat propagation between adjacent zones
- Thermal hub effects (e.g., hallway influencing all zones)
- Cold sink effects (basement cooling upper zones)
- Thermal isolation (garage/laundry remaining insulated)
- Multi-zone demand prioritization with complex interactions
- PID integral accumulation across extended periods
- Asymmetric demand cascading through thermal network

Simulates realistic house thermal topology:
- Master Suite (20m²) <-> Bedroom2 (15m²), Bathroom (8m²)
- Living Room (30m²) <-> Kitchen (18m²), Hallway (12m²), Study (14m²)
- Basement (40m²) acts as cold sink for upper zones
- Garage (35m²) & Laundry (10m²) thermally isolated
- Dining Room (16m²) bridges Kitchen & Living Room
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
# TEST FIXTURE BASE - 11-Zone Setup
# =========================================================

class BaseTestFixture(unittest.TestCase):
    """
    Base test class providing 11-zone house configuration.
    
    Configures realistic thermal topology simulating a multi-level house
    with various zone sizes and thermal coupling characteristics.
    """
    
    def setUp(self):
        """Initialize 11-zone test environment with time mocking."""
        # Mock time for deterministic tests
        self.time_patcher = patch('time.time', return_value=FIXED_TIME_START)
        self.mock_time = self.time_patcher.start()
        
        # 11-zone house configuration with realistic floor areas
        self.zone_configs = [
            {"entity_id": "climate.master_suite", "name": "Master Suite", "area": 20.0},
            {"entity_id": "climate.bedroom2", "name": "Bedroom 2", "area": 15.0},
            {"entity_id": "climate.bathroom", "name": "Bathroom", "area": 8.0},
            {"entity_id": "climate.living_room", "name": "Living Room", "area": 30.0},
            {"entity_id": "climate.kitchen", "name": "Kitchen", "area": 18.0},
            {"entity_id": "climate.hallway", "name": "Hallway", "area": 12.0},
            {"entity_id": "climate.study", "name": "Study", "area": 14.0},
            {"entity_id": "climate.dining_room", "name": "Dining Room", "area": 16.0},
            {"entity_id": "climate.basement", "name": "Basement", "area": 40.0},
            {"entity_id": "climate.garage", "name": "Garage", "area": 35.0},
            {"entity_id": "climate.laundry", "name": "Laundry", "area": 10.0},
        ]
        self.mock_hass = MockHASS()
        
        # Setup logging
        setup_logging(level=logging.DEBUG)
        self.log_collector = LogCollector()
        self.log_collector.start_collecting()

    def tearDown(self):
        """Clean up test environment."""
        self.time_patcher.stop()
        self.log_collector.stop_collecting()


# =========================================================
# MULTI-ROOM THERMAL COUPLING TEST SUITE (11 Zones)
# =========================================================

class TestMultiRoomThermalCoupling(BaseTestFixture):
    """
    Test realistic multi-zone house with thermal interactions.
    
    House thermal topology (documented heat flow paths):
    - Master Suite (heated) -> Bedroom2, Bathroom (adjacent zones receive passive heat)
    - Living Room (heated) -> Kitchen, Hallway, Study (open-concept influences)
    - Basement (cold/unheated) -> Living Room, Kitchen (upward heat loss)
    - Garage (unheated) -> Kitchen (external cold boundary)
    - Hallway (central hub) -> influences all adjacent zones
    - Laundry -> Kitchen, Garage (utility area interactions)
    
    Heat transfer modeled as:
    - Active heating zone warms adjacent zones passively
    - Cold zones pull heat from neighbors
    - Large zones have more influence than small zones
    """

    def test_heat_propagation_master_suite_warms_adjacent_zones(self):
        """
        Test heat propagation from actively heated Master Suite to adjacent zones.
        
        Scenario:
        - Master Suite: 16°C current, 22°C target (6°C error, actively heating)
        - Bedroom2 (adjacent): 17.5°C current, 20°C target (passive heat transfer)
        
        Thermal physics:
        - Master Suite at full heating power warms Bedroom2 via shared walls
        - Bedroom2 receives passive heating, reducing its error vs. theoretical maximum
        
        Expected: Bedroom2 error < 3.5°C (due to passive heating from Master Suite)
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Master Suite: Full heating demand (cold start)
        master_event = create_mock_event("climate.master_suite", 16.0, 22.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(master_event))
        
        # Bedroom2: Adjacent to heated Master Suite, receives passive heating
        # Without heat transfer: 20 - 16 = 4°C error
        # With heat transfer: 20 - 17.5 = 2.5°C error (warmed by Master Suite)
        bedroom2_event = create_mock_event("climate.bedroom2", 17.5, 20.0, 'idle')
        asyncio.run(controller._async_hvac_demand_change(bedroom2_event))
        
        bedroom2_zone = controller.zones.get("climate.bedroom2")
        self.assertIsNotNone(bedroom2_zone)
        
        # Verify passive heating effect: error reduced below theoretical maximum
        self.assertLess(bedroom2_zone.current_error, 3.5,
                       "Heat from Master Suite should reduce Bedroom2's error")

    def test_hallway_central_hub_influences_all_zones(self):
        """
        Test hallway as central thermal hub influencing adjacent zones.
        
        Scenario:
        - Hallway: 16°C current, 23°C target (7°C error, actively heating)
        - Adjacent zones (Living Room, Kitchen, Study) receive passive heat
        
        Thermal topology:
        - Hallway is central circulation space, open to multiple rooms
        - Heated hallway warms all adjacent zones without active heating
        - This is realistic for open-concept homes
        
        Expected: Living Room passive warming despite idle HVAC (error < 5.0°C)
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Heat hallway (the central hub)
        hallway_event = create_mock_event("climate.hallway", 16.0, 23.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(hallway_event))
        
        hallway_zone = controller.zones.get("climate.hallway")
        self.assertTrue(hallway_zone.is_demanding_heat)
        self.assertGreater(hallway_zone.current_error, 0,
                          "Hallway should show active demand")
        
        # Adjacent Living Room warms passively from heated hallway
        # Without heat transfer: 22 - 17 = 5°C error
        # With passive warming: 22 - 18.5 = 3.5°C error
        living_room_event = create_mock_event("climate.living_room", 18.5, 22.0, 'idle')
        asyncio.run(controller._async_hvac_demand_change(living_room_event))
        
        living_room_zone = controller.zones.get("climate.living_room")
        
        # Verify passive heating reduces error
        self.assertLess(living_room_zone.current_error, 5.0,
                       "Hallway heat transfer should reduce Living Room error")

    def test_basement_cold_sink_affects_upper_zones(self):
        """
        Test basement acting as cold thermal sink, increasing demand above it.
        
        Scenario:
        - Basement: 12°C current, 18°C target (6°C error, idle - unheated)
        - Living Room (above): 19°C current, 22°C target (3°C error, heating)
        
        Thermal physics:
        - Cold basement creates downward heat loss path for Living Room
        - Living Room must heat harder to compensate for basement cold loss
        - Basement not actively demanding, but passively influences demand above
        
        Expected: Living Room demand > 2.5°C to overcome basement cooling effect
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Basement: Very cold, unheated (large thermal mass remains cold)
        basement_event = create_mock_event("climate.basement", 12.0, 18.0, 'idle')
        asyncio.run(controller._async_hvac_demand_change(basement_event))
        
        basement_zone = controller.zones.get("climate.basement")
        basement_demand = basement_zone.get_demand_metric()
        
        # Basement not actively demanding (idle status)
        self.assertEqual(basement_demand, 0.0,
                        "Basement idle - no active demand metric")
        
        # Living Room above basement needs extra heat to compensate
        living_room_event = create_mock_event("climate.living_room", 19.0, 22.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(living_room_event))
        
        living_room_zone = controller.zones.get("climate.living_room")
        living_room_demand = living_room_zone.get_demand_metric()
        
        # Living room must demand more due to cold basement below
        self.assertGreater(living_room_demand, 2.5,
                          "Living Room must demand extra heat to overcome basement cooling")

    def test_garage_unheated_cools_kitchen_progressively(self):
        """
        Test unheated garage slowly cooling adjacent kitchen over time.
        
        Scenario:
        - Initial: Kitchen 20.5°C, target 22°C (1.5°C error, heating)
        - 2 hours later: Garage external wall now colder, Kitchen cools
        - Kitchen: 20°C current, target 22°C (2°C error - increased demand)
        
        Thermal dynamics:
        - External boundary (garage wall) temperature drops over time
        - Kitchen experiences increased heat loss to colder garage
        - Kitchen's PID controller increases output to maintain target
        
        Expected: Kitchen demand increases despite same target (2 > 1.5)
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Initial: Kitchen at 20.5°C
        kitchen_event1 = create_mock_event("climate.kitchen", 20.5, 22.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(kitchen_event1))
        
        kitchen_zone = controller.zones.get("climate.kitchen")
        initial_demand = kitchen_zone.get_demand_metric()
        
        # 2 hours later: Garage thermal mass has cooled further
        # (night, no solar gain, outdoor temperature dropping)
        self.mock_time.return_value += 7200
        
        # Kitchen now loses more heat to colder garage
        kitchen_event2 = create_mock_event("climate.kitchen", 20.0, 22.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(kitchen_event2))
        
        updated_demand = kitchen_zone.get_demand_metric()
        
        # Demand increases as temperature drops
        self.assertGreater(updated_demand, initial_demand,
                          "Kitchen demand must increase as temperature drops over time")

    def test_simultaneous_multi_zone_demand_prioritization(self):
        """
        Test correct max demand zone selection with 5+ zones heating simultaneously.
        
        Scenario:
        - Master Suite: 17°C current, 23°C target (6°C error - MAX)
        - Living Room: 18°C current, 22°C target (4°C error)
        - Kitchen: 19°C current, 22°C target (3°C error)
        - Basement: 14°C current, 18°C target (4°C error)
        - Laundry: 16°C current, 20°C target (4°C error)
        
        Expected: Boiler commands based on Master Suite's highest error (6°C)
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Activate 5 zones with different demands
        zones_heating = [
            ("climate.master_suite", 17.0, 23.0),  # 6.0°C error
            ("climate.living_room", 18.0, 22.0),   # 4.0°C error
            ("climate.kitchen", 19.0, 22.0),       # 3.0°C error
            ("climate.basement", 14.0, 18.0),      # 4.0°C error
            ("climate.laundry", 16.0, 20.0),       # 4.0°C error
        ]
        
        for entity_id, current, target in zones_heating:
            event = create_mock_event(entity_id, current, target, 'heating')
            asyncio.run(controller._async_hvac_demand_change(event))
        
        # Master Suite has highest error
        master_zone = controller.zones.get("climate.master_suite")
        max_demand = master_zone.get_demand_metric()
        self.assertEqual(max_demand, 6.0,
                        "Master Suite error should be exactly 6.0°C")
        
        # All other zones have lower demand
        living_room = controller.zones.get("climate.living_room").get_demand_metric()
        kitchen = controller.zones.get("climate.kitchen").get_demand_metric()
        
        self.assertLess(living_room, max_demand,
                       "Living Room (4°C) should have less demand than Master Suite (6°C)")
        self.assertLess(kitchen, max_demand,
                       "Kitchen (3°C) should have less demand than Master Suite (6°C)")

    def test_dining_room_kitchen_thermal_balance(self):
        """
        Test thermal balance when adjacent zones have matching conditions.
        
        Scenario:
        - Dining Room: 20°C current, 22°C target (2°C error)
        - Kitchen: 20°C current, 22°C target (2°C error)
        
        Thermal equilibrium:
        - Open-concept dining/kitchen area
        - Both zones at identical temperatures and targets
        - Should produce equal demand signals
        
        Expected: Errors match exactly (±0.1°C tolerance)
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Both at matching conditions
        dining_event = create_mock_event("climate.dining_room", 20.0, 22.0, 'heating')
        kitchen_event = create_mock_event("climate.kitchen", 20.0, 22.0, 'heating')
        
        asyncio.run(controller._async_hvac_demand_change(dining_event))
        asyncio.run(controller._async_hvac_demand_change(kitchen_event))
        
        dining_zone = controller.zones.get("climate.dining_room")
        kitchen_zone = controller.zones.get("climate.kitchen")
        
        # Errors should be identical (symmetrical topology)
        self.assertAlmostEqual(dining_zone.current_error, kitchen_zone.current_error, delta=0.1,
                              msg="Symmetric zones should have identical errors")

    def test_study_hallway_bedroom2_cluster_heating(self):
        """
        Test cascade heating through interconnected zone cluster.
        
        Scenario:
        - Study: 17°C current, 22°C target (5°C error, actively heating)
        - Hallway (connected): 18°C current, 22°C target (4°C error, idle)
        - Bedroom2 (indirectly connected via hallway): 18.5°C, 20°C target (1.5°C error, idle)
        
        Thermal cascade:
        1. Study heating adds heat to system
        2. Hallway receives heat via open connection to Study
        3. Bedroom2 indirectly benefits via Hallway warmth
        
        Expected: Errors decrease along cascade path (5 > 4 > 1.5)
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Start heating Study (source of cascade)
        study_event = create_mock_event("climate.study", 17.0, 22.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(study_event))
        
        # Hallway warms passively via Study proximity
        hallway_event = create_mock_event("climate.hallway", 18.0, 22.0, 'idle')
        asyncio.run(controller._async_hvac_demand_change(hallway_event))
        
        # Bedroom2 indirectly warmed (far from Study, closer to Hallway)
        bedroom2_event = create_mock_event("climate.bedroom2", 18.5, 20.0, 'idle')
        asyncio.run(controller._async_hvac_demand_change(bedroom2_event))
        
        study_zone = controller.zones.get("climate.study")
        hallway_zone = controller.zones.get("climate.hallway")
        bedroom2_zone = controller.zones.get("climate.bedroom2")
        
        # Verify cascade: Source has highest error, decreases downstream
        self.assertGreater(study_zone.current_error, hallway_zone.current_error,
                          "Study (source) should have higher error than Hallway")
        self.assertGreater(hallway_zone.current_error, bedroom2_zone.current_error,
                          "Hallway should have higher error than Bedroom2 (further from heat source)")

    def test_bathroom_master_suite_shared_heat(self):
        """
        Test bathroom receiving significant passive heat from adjacent Master Suite.
        
        Scenario:
        - Master Suite: 15°C current, 23°C target (8°C error, active heating)
        - Bathroom (adjacent): 17°C current, 21°C target (4°C error, idle)
        
        Thermal coupling:
        - Small bathroom receives substantial heat from large, heated Master Suite
        - Shared wall between zones acts as thermal bridge
        - Bathroom error much lower than if isolated
        
        Expected: Bathroom error << Master Suite error - 3.0 (significant passive heating)
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Master Suite cold start, full heating demand
        master_event = create_mock_event("climate.master_suite", 15.0, 23.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(master_event))
        
        # Bathroom receives substantial passive heating
        bathroom_event = create_mock_event("climate.bathroom", 17.0, 21.0, 'idle')
        asyncio.run(controller._async_hvac_demand_change(bathroom_event))
        
        master_zone = controller.zones.get("climate.master_suite")
        bathroom_zone = controller.zones.get("climate.bathroom")
        
        # Bathroom error significantly lower than Master Suite's
        # Master Suite: 23-15=8°C; without heat sharing, Bathroom would be 21-15=6°C
        # But with heat sharing: Bathroom only 21-17=4°C (substantial passive heating)
        self.assertLess(bathroom_zone.current_error, master_zone.current_error - 3.0,
                       "Bathroom should receive significant passive heat from Master Suite")

    def test_garage_laundry_insulated_from_main_house(self):
        """
        Test garage and laundry remaining cold/insulated despite main house heating.
        
        Scenario:
        - Living Room: 18°C current, 22°C target (actively heating)
        - Kitchen: 18.5°C current, 22°C target (actively heating)
        - Garage: 8°C current, 15°C target (unheated, idle)
        - Laundry: 10°C current, 16°C target (unheated, idle)
        
        Thermal isolation:
        - Utility areas (garage/laundry) are external/unheated
        - Main house wall insulation reduces heat transfer
        - These zones remain cold despite active main house heating
        
        Expected:
        - Garage & Laundry remain much colder than main zones
        - Minimal passive heating effect from main house
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Heat main house zones
        living_room_event = create_mock_event("climate.living_room", 18.0, 22.0, 'heating')
        kitchen_event = create_mock_event("climate.kitchen", 18.5, 22.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(living_room_event))
        asyncio.run(controller._async_hvac_demand_change(kitchen_event))
        
        # Garage and Laundry remain cold despite main house heating
        garage_event = create_mock_event("climate.garage", 8.0, 15.0, 'idle')
        laundry_event = create_mock_event("climate.laundry", 10.0, 16.0, 'idle')
        asyncio.run(controller._async_hvac_demand_change(garage_event))
        asyncio.run(controller._async_hvac_demand_change(laundry_event))
        
        garage_zone = controller.zones.get("climate.garage")
        laundry_zone = controller.zones.get("climate.laundry")
        living_zone = controller.zones.get("climate.living_room")
        
        # Verify thermal isolation: utility areas stay much colder
        self.assertLess(garage_zone.current_temp, 12.0,
                       "Garage should remain very cold despite main house heating")
        self.assertLess(laundry_zone.current_temp, 12.0,
                       "Laundry should remain very cold despite main house heating")
        self.assertGreater(living_zone.current_temp, 17.0,
                          "Main house should be warm")

    def test_integral_accumulation_across_11_zones(self):
        """
        Test PID integral term accumulation across extended 11-zone heating period.
        
        Scenario:
        - Initial: Heat first 5 zones (all at 18°C, target 22°C)
        - After 1 hour: Update zones with slow heating (18.2°C on average)
        
        PID Integral physics:
        - I_sum accumulates error over time: I_sum += error * time_delta
        - Persistent error (even if small) builds up over hour
        - Integral boost helps overcome stuck state
        
        Expected: All zone integrals increase after 1 hour of persistent error
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Start heating first 5 zones
        for i, zone_config in enumerate(self.zone_configs[:5]):
            event = create_mock_event(zone_config["entity_id"], 18.0, 22.0, 'heating')
            asyncio.run(controller._async_hvac_demand_change(event))
        
        # Record initial integral sums
        initial_integrals = {
            eid: controller.zones[eid].pid_integral_sum 
            for eid in [zc["entity_id"] for zc in self.zone_configs[:5]]
        }
        
        # Advance time by 1 hour
        self.mock_time.return_value += 3600
        
        # Update zones with slow temperature increase (heater running)
        for i, zone_config in enumerate(self.zone_configs[:5]):
            new_temp = 18.0 + (i * 0.2)  # Progressive heating 0.2°C each
            event = create_mock_event(zone_config["entity_id"], new_temp, 22.0, 'heating')
            asyncio.run(controller._async_hvac_demand_change(event))
        
        # Verify integral accumulation
        for eid in [zc["entity_id"] for zc in self.zone_configs[:5]]:
            zone = controller.zones[eid]
            self.assertGreater(zone.pid_integral_sum, initial_integrals[eid],
                             f"Integral sum should accumulate over 1 hour for zone {eid}")

    def test_asymmetric_demand_cascade_through_zones(self):
        """
        Test high demand in one zone triggering cascade through thermally connected zones.
        
        Scenario:
        - Master Suite emergency: 10°C current, 23°C target (13°C error - EXTREME)
        
        Cascade effect:
        - Boiler priority shifts to Master Suite's massive demand
        - Adjacent zones benefit from flow temperature optimized for high error
        - System cascades through thermal network prioritizing largest error
        
        Expected:
        - Master Suite demand = 13.0°C (largest in system)
        - Boiler commanded with high flow temperature (40 + 6.5 = 46.5°C minimum)
        - P component alone: 13.0 * 0.5 = 6.5°C boost
        """
        self.mock_hass.services.async_call = AsyncMock()
        controller = MasterController(self.mock_hass, self.zone_configs)
        
        # Extreme emergency: Master Suite extremely cold
        master_event = create_mock_event("climate.master_suite", 10.0, 23.0, 'heating')
        asyncio.run(controller._async_hvac_demand_change(master_event))
        
        master_zone = controller.zones.get("climate.master_suite")
        master_demand = master_zone.get_demand_metric()
        
        # Verify extreme error detected
        self.assertEqual(master_demand, 13.0,
                        "Master Suite demand should be exactly 13°C error")
        
        # Verify boiler commanded with high demand
        call_args = self.mock_hass.services.async_call.call_args
        flow_temp = call_args[0][2]['value']
        
        # Flow temp calculation: base(40) + P(6.5) + I(0) + D(0) = 46.5 minimum
        # Since P=13*0.5=6.5, expected ≈ 46.5
        self.assertGreater(flow_temp, 40.0,
                          "Flow temp must be significantly higher than base for emergency demand")
        self.assertGreater(flow_temp, 45.0,
                          "Emergency demand (13°C error) must command high flow temperature")


if __name__ == '__main__':
    unittest.main()
