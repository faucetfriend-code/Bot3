#!/usr/bin/env python3
"""
Quick usage guide for AVAX grid diagnostic tools.
"""

import os
import subprocess
import sys

def main():
    print("AVAX Grid Rejection Diagnostic Tools")
    print("=" * 50)
    print()
    
    print("AVAILABLE TOOLS:")
    print("1. Simple Diagnostic (Recommended)")
    print("2. Comprehensive Diagnostic")  
    print("3. View README Documentation")
    print("4. Exit")
    print()
    
    while True:
        choice = input("Select option (1-4): ").strip()
        
        if choice == "1":
            print("\nRunning Simple AVAX Diagnostic...")
            print("Make sure trading bot is running on port 8000!")
            print()
            
            try:
                subprocess.run([sys.executable, "simple_avax_diagnostic.py"], check=False)
            except KeyboardInterrupt:
                print("\nDiagnostic interrupted.")
            break
            
        elif choice == "2":
            print("\nRunning Comprehensive Diagnostic...")
            print("Note: This may have dependency issues.")
            print()
            
            try:
                subprocess.run([sys.executable, "diagnose_grid_rejection.py"], check=False)
            except KeyboardInterrupt:
                print("\nDiagnostic interrupted.")
            break
            
        elif choice == "3":
            readme_file = "AVAX_DIAGNOSTIC_README.md"
            if os.path.exists(readme_file):
                print(f"\nOpening {readme_file}...")
                
                # Try to open in default viewer or display content
                try:
                    if os.name == 'nt':  # Windows
                        os.startfile(readme_file)
                    else:  # Mac/Linux
                        subprocess.run(['xdg-open', readme_file], check=False)
                except:
                    # Fallback: display content
                    with open(readme_file, 'r') as f:
                        content = f.read()
                        print(content[:2000] + "\n... (truncated)")  # First 2000 chars
            else:
                print(f"ERROR: {readme_file} not found")
            break
            
        elif choice == "4":
            print("Exiting...")
            break
            
        else:
            print("Invalid choice. Please select 1-4.")
    
    print("\n" + "=" * 50)
    print("For manual API checks:")
    print("  curl http://localhost:8000/api/status")
    print("  curl http://localhost:8000/api/grids")
    print()
    print("Common issues:")
    print("  - Grid state sync problems")
    print("  - API server integration issues") 
    print("  - Database vs memory inconsistencies")
    print("=" * 50)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)