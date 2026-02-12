#!/usr/bin/env python3
"""
AVAX Grid State Diagnostic

Investigates where the AVAX grid state is actually stored and why there's inconsistency.
"""

import os
import sys
import sqlite3
import json
from datetime import datetime

def diagnose_avax_grid_state():
    """
    Comprehensive diagnosis of AVAX grid state issue.
    """
    print("AVAX Grid State Diagnostic")
    print("=" * 50)
    
    # 1. Check database for any AVAX records
    print("\n1. DATABASE CHECK:")
    print("-" * 20)
    
    db_paths = [
        os.path.join(os.getcwd(), 'trading_bot_v2', 'trading_bot.db'),
        os.path.join(os.getcwd(), 'trading_bot.db'),
        os.path.join(os.path.dirname(__file__), 'trading_bot_v2', 'trading_bot.db'),
    ]
    
    db_found = False
    for db_path in db_paths:
        if os.path.exists(db_path):
            print(f"Found database: {db_path}")
            db_found = True
            break
    
    if not db_found:
        print("No database file found")
    else:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Check all tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            print(f"Tables found: {tables}")
            
            # Check grid_states table
            if 'grid_states' in tables:
                cursor.execute("SELECT symbol, state, created_at FROM grid_states")
                grid_records = cursor.fetchall()
                print(f"Grid records: {len(grid_records)}")
                for record in grid_records:
                    print(f"  - {record[0]}: {record[1]} (created: {record[2]})")
            
            # Check for any table containing AVAX
            for table in tables:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE symbol LIKE '%AVAX%'")
                    count = cursor.fetchone()[0]
                    if count > 0:
                        print(f"AVAX records in {table}: {count}")
                        cursor.execute(f"SELECT * FROM {table} WHERE symbol LIKE '%AVAX%' LIMIT 3")
                        records = cursor.fetchall()
                        for record in records:
                            print(f"  {record}")
                except:
                    pass  # Skip tables without symbol column
            
            conn.close()
            
        except Exception as e:
            print(f"Database error: {e}")
    
    # 2. Check for running trading bot processes
    print("\n2. RUNNING PROCESSES:")
    print("-" * 25)
    
    # This would require psutil, but we can check for common patterns
    try:
        import subprocess
        result = subprocess.run(['tasklist'], capture_output=True, text=True)
        processes = result.stdout
        
        if 'python' in processes.lower() and any(x in processes.lower() for x in ['trading', 'bot', 'grid']):
            print("Found potential trading bot processes:")
            for line in processes.split('\n'):
                if 'python' in line.lower() and any(x in line.lower() for x in ['trading', 'bot', 'grid']):
                    print(f"  {line.strip()}")
        else:
            print("No obvious trading bot processes found")
            
    except:
        print("Could not check running processes")
    
    # 3. Check for grid state files or logs
    print("\n3. FILE SYSTEM CHECK:")
    print("-" * 22)
    
    # Look for files that might contain grid state
    search_dirs = [
        os.getcwd(),
        os.path.join(os.getcwd(), 'trading_bot_v2'),
        os.path.dirname(__file__),
        os.path.join(os.path.dirname(__file__), 'trading_bot_v2'),
    ]
    
    grid_files_found = []
    
    for search_dir in search_dirs:
        if not os.path.exists(search_dir):
            continue
            
        print(f"Searching in: {search_dir}")
        
        try:
            for item in os.listdir(search_dir):
                item_path = os.path.join(search_dir, item)
                
                # Check for grid-related files
                if any(keyword in item.lower() for keyword in ['grid', 'avax', 'state']):
                    if os.path.isfile(item_path):
                        size = os.path.getsize(item_path)
                        modified = datetime.fromtimestamp(os.path.getmtime(item_path))
                        print(f"  File: {item} ({size} bytes, modified: {modified})")
                        grid_files_found.append(item_path)
                    elif os.path.isdir(item_path):
                        print(f"  Dir:  {item}/")
                        
                        # Check contents for grid files
                        try:
                            for subitem in os.listdir(item_path):
                                if any(keyword in subitem.lower() for keyword in ['grid', 'avax', 'state']):
                                    subpath = os.path.join(item_path, subitem)
                                    if os.path.isfile(subpath):
                                        size = os.path.getsize(subpath)
                                        modified = datetime.fromtimestamp(os.path.getmtime(subpath))
                                        print(f"    File: {subitem} ({size} bytes, modified: {modified})")
                        except:
                            pass
                            
        except Exception as e:
            print(f"  Error searching {search_dir}: {e}")
    
    # 4. Check for any JSON or pickle files that might contain grid state
    print("\n4. SERIALIZED DATA CHECK:")
    print("-" * 26)
    
    for search_dir in search_dirs:
        if not os.path.exists(search_dir):
            continue
            
        try:
            for item in os.listdir(search_dir):
                item_path = os.path.join(search_dir, item)
                
                if not os.path.isfile(item_path):
                    continue
                    
                # Check JSON files
                if item.lower().endswith('.json'):
                    try:
                        with open(item_path, 'r') as f:
                            content = f.read()
                            if any(keyword in content.lower() for keyword in ['avax', 'grid', 'active']):
                                print(f"JSON with grid data: {item}")
                                # Show first relevant line
                                for line in content.split('\n')[:10]:
                                    if any(keyword in line.lower() for keyword in ['avax', 'grid', 'active']):
                                        print(f"  {line.strip()}")
                                        break
                    except:
                        pass
                
                # Check pickle files (might contain grid state)
                elif item.lower().endswith('.pkl') or item.lower().endswith('.pickle'):
                    print(f"Pickle file (potential grid state): {item}")
                    
        except Exception as e:
            print(f"Error checking {search_dir}: {e}")
    
    # 5. Check logs for AVAX grid activity
    print("\n5. LOG ANALYSIS:")
    print("-" * 16)
    
    log_extensions = ['.log', '.txt']
    log_keywords = ['avax', 'grid', 'active', 'trading']
    
    for search_dir in search_dirs:
        if not os.path.exists(search_dir):
            continue
            
        try:
            for item in os.listdir(search_dir):
                item_path = os.path.join(search_dir, item)
                
                if not os.path.isfile(item_path):
                    continue
                    
                # Check if it's a log file
                if any(item.lower().endswith(ext) for ext in log_extensions) or 'log' in item.lower():
                    try:
                        with open(item_path, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = f.readlines()[-50:]  # Last 50 lines
                            
                            for i, line in enumerate(lines):
                                if any(keyword in line.lower() for keyword in log_keywords):
                                    print(f"Found relevant log in {item}:")
                                    print(f"  Line {len(lines)-i}: {line.strip()}")
                                    break
                                    
                    except Exception as e:
                        print(f"Error reading log {item}: {e}")
                        
        except Exception as e:
            print(f"Error searching logs in {search_dir}: {e}")
    
    # 6. Summary and recommendations
    print("\n6. SUMMARY & RECOMMENDATIONS:")
    print("-" * 33)
    
    print("Based on the investigation:")
    
    if not db_found:
        print("- No database found - grid state might be purely in-memory")
        print("- RECOMMENDATION: Check if trading bot is running")
        print("- RECOMMENDATION: Grid state resets when bot restarts")
    
    elif grid_files_found:
        print(f"- Found {len(grid_files_found)} grid-related files")
        print("- RECOMMENDATION: Check these files for AVAX grid state")
    
    else:
        print("- Database exists but no grid records found")
        print("- RECOMMENDATION: AVAX grid might be in-memory only")
        print("- RECOMMENDATION: Need to identify where grid manager loads state")
    
    print("\nNEXT STEPS:")
    print("1. Start the trading bot if not running")
    print("2. Monitor when AVAX grid gets created")
    print("3. Check if grid state persists to database")
    print("4. If in-memory only, need to fix persistence issue")
    
    return True

if __name__ == "__main__":
    try:
        diagnose_avax_grid_state()
    except KeyboardInterrupt:
        print("\nDiagnostic interrupted by user")
    except Exception as e:
        print(f"\nDiagnostic error: {e}")
    finally:
        input("\nPress Enter to exit...")