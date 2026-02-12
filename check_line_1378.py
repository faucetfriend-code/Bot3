#!/usr/bin/env python3
"""
Debug script to check line 1378 context in trading_bot.py
"""

import os
from pathlib import Path

# Read the trading_bot.py file and check what's around line 1378
bot_file = Path(__file__).parent / "trading_bot_v2" / "trading_bot.py"

if bot_file.exists():
    with open(bot_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    print(f"Lines around 1378 in {bot_file}:")
    start_line = max(0, 1378 - 10)
    end_line = min(len(lines), 1378 + 10)
    
    for i in range(start_line, end_line):
        marker = ">>> " if i == 1377 else "    "  # Line 1378 is index 1377
        print(f"{marker}{i+1:4d}: {lines[i].rstrip()}")
else:
    print(f"File not found: {bot_file}")