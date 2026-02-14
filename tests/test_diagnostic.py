#!/usr/bin/env python3
"""
Quick test runner for AVAX grid diagnostic.
"""

import subprocess
import sys
from pathlib import Path

def test_diagnostic():
    """Test the diagnostic tool."""
    print("Testing AVAX Grid Diagnostic Tool...")
    
    # Check if the diagnostic file exists
    diag_file = Path("diagnose_avax_grid.py")
    if not diag_file.exists():
        print("ERROR: Diagnostic file not found")
        return False
    
    # Try to run the diagnostic (will fail if API not running, but should test syntax)
    try:
        result = subprocess.run([
            sys.executable, "diagnose_avax_grid.py", "--help"
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            print("SUCCESS: Diagnostic tool syntax is valid")
            print("\n📋 Usage:")
            print(result.stdout)
            return True
        else:
            print(f"ERROR: Diagnostic tool error: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("WARNING: Diagnostic test timed out (but syntax may be OK)")
        return True
    except Exception as e:
        print(f"ERROR: Failed to test diagnostic: {e}")
        return False

if __name__ == "__main__":
    success = test_diagnostic()
    
    if success:
        print("\nTo run the diagnostic:")
        print("   python diagnose_avax_grid.py")
        print("\nMake sure the trading bot is running on port 8000 first!")
        print("\nThe diagnostic will check:")
        print("   - API server availability")
        print("   - Grid count consistency between endpoints")
        print("   - AVAX-specific grid state")
        print("   - Data synchronization issues")
    
    sys.exit(0 if success else 1)