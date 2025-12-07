#!/usr/bin/env python3
"""
Unified Test Runner with CLI Parameter Support

Usage:
  python3 run_tests.py                                    # Run all tests
  python3 run_tests.py --module zone                      # Run zone-related tests
  python3 run_tests.py --test test_error_and_demand      # Run specific test
  python3 run_tests.py --module master --log-level INFO   # Run with specific log level
  python3 run_tests.py --output-dir ./custom_results     # Custom output directory
"""

import sys
import argparse
import logging
from pathlib import Path
from test_helpers import TestExecutor, setup_logging

def main():
    parser = argparse.ArgumentParser(
        description="Unified Test Runner for SmartHeatingController",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES:
  Run all tests:
    python3 run_tests.py

  Run specific module:
    python3 run_tests.py --module zone
    python3 run_tests.py --module master

  Run specific test by name:
    python3 run_tests.py --test test_error_and_demand_calculation
    python3 run_tests.py --test test_pid_control

  Combine filters:
    python3 run_tests.py --module zone --test demand

  Custom output directory:
    python3 run_tests.py --output-dir /path/to/results

  Set logging level:
    python3 run_tests.py --log-level DEBUG
    python3 run_tests.py --log-level INFO
        """
    )
    
    parser.add_argument(
        '--module',
        '-m',
        nargs='+',
        help='Test module name(s) to run (e.g., "zone" for tests_zone_*.py, "master" for tests_master_*.py)'
    )
    
    parser.add_argument(
        '--test',
        '-t',
        nargs='+',
        help='Specific test name(s) to run (e.g., "test_error_and_demand", "test_pid_control")'
    )
    
    parser.add_argument(
        '--output-dir',
        '-o',
        default=None,
        help='Output directory for test results (default: ./test_results)'
    )
    
    parser.add_argument(
        '--log-level',
        '-l',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='DEBUG',
        help='Logging level for test output (default: DEBUG)'
    )
    
    parser.add_argument(
        '--no-timestamp',
        action='store_true',
        help='Disable timestamp in output filename (default: timestamp enabled)'
    )
    
    parser.add_argument(
        '--list-modules',
        action='store_true',
        help='List available test modules and exit'
    )
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = getattr(logging, args.log_level)
    setup_logging(level=log_level)
    
    # Setup test executor
    test_dir = Path(__file__).parent
    executor = TestExecutor(str(test_dir))
    
    # Override output directory if provided
    if args.output_dir:
        executor.results_dir = Path(args.output_dir)
        executor.results_dir.mkdir(parents=True, exist_ok=True)
    
    # List available modules if requested
    if args.list_modules:
        available_modules = executor.discover_test_modules()
        print("\nAvailable Test Modules:")
        print("-" * 80)
        for i, module in enumerate(available_modules, 1):
            print(f"  {i}. {module}")
        print("-" * 80)
        print(f"\nTotal: {len(available_modules)} test modules\n")
        return 0
    
    # Run tests
    try:
        exit_code = executor.run_tests(
            module_patterns=args.module,
            test_names=args.test
        )
        return exit_code
    except Exception as e:
        print(f"Error running tests: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())
