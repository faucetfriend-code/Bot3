#!/usr/bin/env python3
"""
Import verification script for trading bot modules.
Tests that all core modules can be imported successfully.
"""

import sys
import traceback
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_import(module_name, description):
    """Test importing a module and report result."""
    try:
        __import__(module_name)
        print(f"[OK] {description}: {module_name}")
        return True
    except ImportError as e:
        print(f"[FAIL] {description}: {module_name} - {e}")
        return False
    except Exception as e:
        print(f"[ERROR] {description}: {module_name} - Unexpected error: {e}")
        return False

def main():
    """Run import tests for all core modules."""
    print("Testing core module imports...")
    print("=" * 50)

    results = []

    # Core modules
    results.append(test_import("config", "Configuration module"))
    results.append(test_import("database", "Database manager"))
    results.append(test_import("models", "Data models"))
    results.append(test_import("audit", "Audit logging"))
    results.append(test_import("auth", "Authentication"))
    results.append(test_import("encryption", "Encryption utilities"))
    results.append(test_import("key_rotation", "Key rotation"))
    results.append(test_import("main", "Main trading bot"))
    results.append(test_import("subaccount_manager", "Subaccount manager"))
    results.append(test_import("balance_manager", "Balance manager"))

    # API related
    results.append(test_import("api_server", "API server"))
    results.append(test_import("pacifica_client", "Pacifica client"))
    results.append(test_import("pacifica_market_data", "Pacifica market data"))
    results.append(test_import("market_data_auto_collector", "Auto market data collector"))
    results.append(test_import("market_data_collector", "Market data collector"))
    results.append(test_import("pacifica_validator", "Pacifica validator"))

    # Utils
    results.append(test_import("utils.symbol_utils", "Symbol utilities"))

    print("=" * 50)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} modules imported successfully")

    if passed == total:
        print("SUCCESS: All imports successful!")
        return 0
    else:
        print("FAILURE: Some imports failed. Check the output above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())