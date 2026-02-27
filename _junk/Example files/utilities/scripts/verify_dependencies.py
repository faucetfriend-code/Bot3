#!/usr/bin/env python3
"""
Dependency verification script for trading bot.
Tests all critical imports to ensure dependencies are properly installed.
"""

import sys
import importlib
from pathlib import Path

# Add project root to Python path for internal modules
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Core dependencies to test
CORE_DEPS = [
    'fastapi',
    'uvicorn',
    'pydantic',
    'dotenv',
    'aiosqlite',
    'websockets',
    'aiohttp',
    'jose',
    'passlib',
    'cryptography',
    'loguru',
    'structlog',
    'slowapi',
    'base58',
    'solders',
    'requests',
    'psutil',
    'dateutil',
]

# Optional dependencies (may fail on some systems)
OPTIONAL_DEPS = [
    'numpy',
    'pandas',
    'talib',  # TA-Lib
    'ccxt',
]

# Internal modules to test
INTERNAL_MODULES = [
    'config',
    'database',
    'auth',
    'models',
    'api_server',
    'main',
    'strategy',
    'risk',
    'execution',
    'market_data_collector',
    'balance_manager',
    'subaccount_manager',
]

def test_imports(modules, name):
    """Test importing a list of modules."""
    failed = []
    for module in modules:
        try:
            importlib.import_module(module)
            print(f"OK {module}")
        except ImportError as e:
            print(f"FAIL {module}: {e}")
            failed.append(module)
        except Exception as e:
            print(f"WARN {module}: Unexpected error - {e}")
            failed.append(module)

    if failed:
        print(f"\n{len(failed)} {name} modules failed to import: {', '.join(failed)}")
    else:
        print(f"\nAll {name} modules imported successfully!")

    return len(failed) == 0

def main():
    print("Verifying trading bot dependencies...\n")

    # Test core dependencies
    print("Core Dependencies:")
    core_ok = test_imports(CORE_DEPS, "core")

    # Test optional dependencies
    print("\nOptional Dependencies:")
    optional_ok = test_imports(OPTIONAL_DEPS, "optional")

    # Test internal modules
    print("\nInternal Modules:")
    internal_ok = test_imports(INTERNAL_MODULES, "internal")

    print(f"\n{'='*50}")
    if core_ok and internal_ok:
        print("Core functionality should work!")
        if not optional_ok:
            print("Some optional features may not be available (TA-Lib, numpy, etc.)")
        return 0
    else:
        print("Critical dependencies or modules are missing!")
        print("The trading bot may not function properly.")
        return 1

if __name__ == "__main__":
    sys.exit(main())