"""
Unified Test Infrastructure for SmartHeatingController

This module provides:
- Base test fixture classes
- Logging infrastructure
- Test execution helpers
- Common mock utilities
"""

import unittest
import logging
import json
import sys
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path
from unittest.mock import patch

# =========================================================
# LOGGING INFRASTRUCTURE
# =========================================================

class HACompatibleFormatter(logging.Formatter):
    """Formatter compatible with Home Assistant logging format."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record in HA-compatible JSON format."""
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        return json.dumps(log_entry)


class LogCollector:
    """Collects logs in memory for testing purposes."""
    
    def __init__(self):
        self.logs: List[Dict[str, Any]] = []
        self.handler: Optional[logging.Handler] = None
        
    def start_collecting(self, logger_name: str = "don_controller") -> None:
        """Start collecting logs from the specified logger."""
        logger = logging.getLogger(logger_name)
        
        # Custom handler that appends to our logs list
        class CollectorHandler(logging.Handler):
            def __init__(self, collector: "LogCollector"):
                super().__init__()
                self.collector = collector
                
            def emit(self, record: logging.LogRecord):
                log_entry = {
                    "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                    "module": record.module,
                    "function": record.funcName,
                    "line": record.lineno,
                }
                self.collector.logs.append(log_entry)
        
        self.handler = CollectorHandler(self)
        logger.addHandler(self.handler)
        
    def stop_collecting(self, logger_name: str = "don_controller") -> None:
        """Stop collecting logs."""
        logger = logging.getLogger(logger_name)
        if self.handler:
            logger.removeHandler(self.handler)
            
    def get_logs(self) -> List[Dict[str, Any]]:
        """Get all collected logs."""
        return self.logs
    
    def clear_logs(self) -> None:
        """Clear collected logs."""
        self.logs = []


def setup_logging(level: int = logging.DEBUG) -> None:
    """Configure logging for tests."""
    logger = logging.getLogger("don_controller")
    logger.setLevel(level)
    
    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Add console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(HACompatibleFormatter())
    logger.addHandler(console_handler)


# =========================================================
# BASE TEST FIXTURE
# =========================================================

class UnifiedTestFixture(unittest.TestCase):
    """Base class for all test suites with unified setup/teardown."""
    
    def setUp(self):
        """Set up test environment."""
        self.time_patcher = patch('time.time', return_value=1672531200.0)  # 2023-01-01T00:00:00
        self.mock_time = self.time_patcher.start()
        
        # Setup logging collection
        setup_logging(level=logging.DEBUG)
        self.log_collector = LogCollector()
        self.log_collector.start_collecting()

    def tearDown(self):
        """Clean up test environment."""
        self.time_patcher.stop()
        self.log_collector.stop_collecting()
    
    def assert_test_passes(self, description: str) -> bool:
        """Helper to document test passing with specific coverage description."""
        logs = self.log_collector.get_logs()
        self.assertGreater(len(logs), 0, f"Test '{description}' has no log output")
        return True
    
    def get_logs(self) -> List[Dict[str, Any]]:
        """Get all collected logs for this test."""
        return self.log_collector.get_logs()


# =========================================================
# TEST RESULT TRACKING
# =========================================================

class DetailedTestResult(unittest.TextTestResult):
    """Custom test result class that captures detailed test information."""
    
    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.test_details: List[Dict[str, Any]] = []
        self.current_log_collector: Optional[LogCollector] = None
    
    def startTest(self, test):
        super().startTest(test)
        self.current_test_start = datetime.now()
        # Create fresh log collector for each test
        self.current_log_collector = LogCollector()
        self.current_log_collector.start_collecting()
    
    def stopTest(self, test):
        super().stopTest(test)
        duration = (datetime.now() - self.current_test_start).total_seconds()
        
        # Get logs for this test
        test_logs = self.current_log_collector.get_logs() if self.current_log_collector else []
        self.current_log_collector.stop_collecting()
        
        # Check if test passed
        is_success = True
        error_msg = None
        for failure_test, trace in self.failures + self.errors:
            if failure_test == test:
                is_success = False
                error_msg = trace
                break
        
        test_info = {
            'name': str(test),
            'docstring': test._testMethodDoc,
            'duration': duration,
            'logs': test_logs,
            'result': 'PASS' if is_success else 'FAIL',
            'error_msg': error_msg
        }
        self.test_details.append(test_info)


class DetailedTestRunner(unittest.TextTestRunner):
    """Custom test runner that logs to file with detailed information."""
    
    def __init__(self, stream=None, descriptions=True, verbosity=2, log_file=None):
        self.log_file = log_file
        super().__init__(stream=stream, descriptions=descriptions, verbosity=verbosity)
    
    def _makeResult(self):
        return DetailedTestResult(self.stream, self.descriptions, self.verbosity)
    
    def run(self, test):
        result = super().run(test)
        self._write_detailed_report(result)
        return result
    
    def _write_detailed_report(self, result):
        """Write detailed test report to file."""
        if not self.log_file:
            return
        
        with open(self.log_file, 'w', encoding='utf-8') as f:
            f.write("=" * 120 + "\n")
            f.write("COMPREHENSIVE UNIT TEST EXECUTION REPORT\n")
            f.write("=" * 120 + "\n\n")
            
            f.write(f"Execution Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Total Tests: {result.testsRun}\n")
            f.write(f"Passed: {result.testsRun - len(result.failures) - len(result.errors)}\n")
            f.write(f"Failed: {len(result.failures)}\n")
            f.write(f"Errors: {len(result.errors)}\n\n")
            
            f.write("=" * 120 + "\n")
            f.write("DETAILED TEST RESULTS WITH LOGGING\n")
            f.write("=" * 120 + "\n\n")
            
            for i, test_detail in enumerate(result.test_details, 1):
                self._write_test_detail(f, i, test_detail)
            
            # Summary
            f.write("\n" + "=" * 120 + "\n")
            f.write("EXECUTION SUMMARY\n")
            f.write("=" * 120 + "\n\n")
            
            passed_tests = [t for t in result.test_details if t['result'] == 'PASS']
            failed_tests = [t for t in result.test_details if t['result'] == 'FAIL']
            
            f.write(f"Total Executed: {len(result.test_details)}\n")
            f.write(f"Passed: {len(passed_tests)} ({100*len(passed_tests)/len(result.test_details):.1f}%)\n")
            f.write(f"Failed: {len(failed_tests)} ({100*len(failed_tests)/len(result.test_details):.1f}%)\n\n")
            
            if failed_tests:
                f.write("FAILED TESTS:\n")
                for test in failed_tests:
                    f.write(f"  - {test['name']}\n")
    
    def _write_test_detail(self, f, test_num, test_detail):
        """Write detailed information for a single test."""
        f.write(f"\n{'='*120}\n")
        f.write(f"[TEST {test_num}] {test_detail['name']}\n")
        f.write(f"{'='*120}\n\n")
        
        # Test description (docstring)
        if test_detail['docstring']:
            f.write("TEST PURPOSE & COVERAGE:\n")
            f.write("-" * 120 + "\n")
            f.write(test_detail['docstring'].strip())
            f.write("\n\n")
        
        # Test result
        f.write(f"RESULT: {test_detail['result']} (Duration: {test_detail['duration']:.4f}s)\n\n")
        
        # Log output
        if test_detail['logs']:
            f.write("DETAILED LOGGING OUTPUT (Showing How Test Validates Coverage):\n")
            f.write("-" * 120 + "\n")
            for i, log_entry in enumerate(test_detail['logs'], 1):
                timestamp = log_entry.get('timestamp', 'unknown')
                level = log_entry.get('level', 'INFO')
                message = log_entry.get('message', '')
                module = log_entry.get('module', '')
                func = log_entry.get('function', '')
                line = log_entry.get('line', 0)
                
                f.write(f"  [{i}] {timestamp} | {level:8} | {module}.{func}:{line}\n")
                f.write(f"      └─ {message}\n\n")
            f.write("-" * 120 + "\n\n")
        else:
            f.write("(No logging output captured - test passed with assertions only)\n\n")
        
        # Error details if failed
        if test_detail['result'] == 'FAIL' and test_detail['error_msg']:
            f.write("ERROR DETAILS:\n")
            f.write("-" * 120 + "\n")
            f.write(test_detail['error_msg'])
            f.write("\n" + "-" * 120 + "\n\n")


# =========================================================
# TEST RUNNER WITH CLI SUPPORT
# =========================================================

class TestExecutor:
    """Unified test execution framework with CLI parameter support."""
    
    def __init__(self, test_dir: str = "."):
        self.test_dir = test_dir
        self.test_modules: List[str] = []
        self.results_dir = Path(test_dir) / "test_results"
        self.results_dir.mkdir(exist_ok=True)
    
    def discover_test_modules(self) -> List[str]:
        """Discover all test modules (test_*.py files)."""
        test_files = sorted(Path(self.test_dir).glob("test_*.py"))
        self.test_modules = [f.stem for f in test_files if f.stem != "test_helpers"]
        return self.test_modules
    
    def run_tests(self, module_patterns: Optional[List[str]] = None, 
                  test_names: Optional[List[str]] = None) -> int:
        """
        Run tests with optional filtering.
        
        Args:
            module_patterns: List of module name patterns to run (e.g., ['zone', 'master'])
            test_names: List of specific test names to run (e.g., ['test_error_and_demand_calculation'])
        
        Returns:
            0 if all tests pass, 1 otherwise
        """
        # Discover available modules
        available_modules = self.discover_test_modules()
        
        # Filter modules if patterns provided
        if module_patterns:
            modules_to_run = []
            for pattern in module_patterns:
                matching = [m for m in available_modules if pattern.lower() in m.lower()]
                modules_to_run.extend(matching)
            modules_to_run = list(set(modules_to_run))  # Remove duplicates
        else:
            modules_to_run = available_modules
        
        # Load tests
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        
        for module_name in modules_to_run:
            try:
                module = __import__(module_name)
                module_suite = loader.loadTestsFromModule(module)
                
                # Filter by test names if provided
                if test_names:
                    filtered_suite = unittest.TestSuite()
                    for test_group in module_suite:
                        for test in test_group:
                            test_method = test._testMethodName
                            if any(name in test_method for name in test_names):
                                filtered_suite.addTest(test)
                    suite.addTests(filtered_suite)
                else:
                    suite.addTests(module_suite)
            except ImportError as e:
                print(f"Warning: Could not import {module_name}: {e}")
        
        # Generate timestamped output file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = self.results_dir / f"test_results_{timestamp}.txt"
        
        # Run tests
        runner = DetailedTestRunner(
            verbosity=2,
            log_file=str(output_file),
            stream=sys.stdout
        )
        
        print(f"\n{'='*120}")
        print(f"Running {suite.countTestCases()} tests")
        print(f"Modules: {', '.join(modules_to_run)}")
        if test_names:
            print(f"Test filters: {', '.join(test_names)}")
        print(f"Results will be saved to:\n  {output_file}")
        print(f"{'='*120}\n")
        
        result = runner.run(suite)
        
        # Print summary
        print("\n" + "=" * 120)
        print("TEST EXECUTION COMPLETED")
        print("=" * 120)
        print(f"Total: {result.testsRun}")
        print(f"Passed: {result.testsRun - len(result.failures) - len(result.errors)}")
        print(f"Failed: {len(result.failures)}")
        print(f"Errors: {len(result.errors)}")
        print(f"\nDetailed report:\n  {output_file}")
        print("=" * 120 + "\n")
        
        return 0 if result.wasSuccessful() else 1


# =========================================================
# MOCK UTILITIES
# =========================================================

class MockState:
    """Mock Home Assistant state object."""
    
    def __init__(self, entity_id: str, attributes: Optional[Dict[str, Any]] = None):
        self.entity_id = entity_id
        self.attributes = attributes or {}
        self.state = str(attributes.get('state', '')) if attributes else ''
    
    def __getitem__(self, key):
        return self.attributes.get(key)
    
    def get(self, key, default=None):
        return self.attributes.get(key, default)
    
    def __repr__(self):
        return f"<MockState entity_id='{self.entity_id}' attrs={self.attributes}>"


class MockHASS:
    """Mock Home Assistant Core object for service calls."""
    
    def __init__(self):
        from unittest.mock import MagicMock
        # Mock the service call interface
        self.services = MagicMock()
        # Mock the event tracking setup
        self.helpers = MagicMock()

    async def async_call(self, domain, service, service_data, blocking):
        """Stub for hass.services.async_call, records the call."""
        from unittest.mock import MagicMock
        return self.services.async_call(domain, service, service_data, blocking)


def create_mock_event(entity_id: str, current_temp: float, target_temp: float, hvac_action: str):
    """Helper to create a mock event dictionary for controller input."""
    from unittest.mock import MagicMock
    state = MockState(entity_id, attributes={
        'current_temperature': current_temp,
        'temperature': target_temp,
        'hvac_action': hvac_action
    })
    return MagicMock(data={'entity_id': entity_id, 'new_state': state})


# Fixed time reference for consistent test behavior
FIXED_TIME_START = 1672531200.0  # 2023-01-01T00:00:00 UTC
