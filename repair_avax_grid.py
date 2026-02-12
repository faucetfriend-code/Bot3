#!/usr/bin/env python3
"""
AVAX Grid Repair Tool

Fixes the specific issue where AVAX has an active grid but no center price,
causing signals to be rejected while the interface shows 0 active grids.

Usage:
    python repair_avax_grid.py --check    # Check for broken grids
    python repair_avax_grid.py --fix      # Fix broken grids
    python repair_avax_grid.py --remove   # Remove broken grids
"""

import argparse
import sys
import os
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add trading_bot_v2 to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'trading_bot_v2'))

try:
    from trading_bot_v2.grid_lifecycle_manager import GridLifecycleManager, GridState
    from loguru import logger
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure trading_bot_v2 module is available")
    sys.exit(1)


class AVAXGridRepair:
    """Tool to diagnose and repair AVAX grid center price issues."""
    
    def __init__(self):
        self.grid_manager = None
        self.broken_grids = []
        
    def initialize(self):
        """Initialize the grid lifecycle manager."""
        try:
            # Initialize with mock client and risk manager for diagnostic
            self.grid_manager = GridLifecycleManager(client=None, risk_manager=None)
            logger.info("GridLifecycleManager initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize GridLifecycleManager: {e}")
            return False
    
    def check_broken_grids(self) -> List[Dict[str, Any]]:
        """Check for grids with missing center prices."""
        if not self.grid_manager:
            logger.error("Grid manager not initialized")
            return []
        
        broken_grids = []
        
        try:
            # Access the internal grids dictionary
            all_grids = getattr(self.grid_manager, '_grids', {})
            
            for symbol, grid_data in all_grids.items():
                # Check if grid is active but missing center price
                if grid_data.get('state') == GridState.ACTIVE:
                    center_price = grid_data.get('center_price')
                    initial_center = grid_data.get('initial_center')
                    
                    if center_price is None and initial_center is None:
                        broken_grids.append({
                            'symbol': symbol,
                            'state': grid_data.get('state').value,
                            'grid_data': grid_data,
                            'issue': 'Missing center_price and initial_center',
                            'created_at': grid_data.get('created_at'),
                            'emergency_stop': grid_data.get('emergency_stop', False)
                        })
                        logger.warning(f"Found broken grid for {symbol}: Active but no center price")
            
            self.broken_grids = broken_grids
            return broken_grids
            
        except Exception as e:
            logger.error(f"Error checking broken grids: {e}")
            return []
    
    def fix_grid_center(self, symbol: str, current_price: Optional[float] = None) -> bool:
        """Fix a broken grid by setting its center price."""
        if not self.grid_manager:
            logger.error("Grid manager not initialized")
            return False
        
        try:
            # Access internal grids
            all_grids = getattr(self.grid_manager, '_grids', {})
            
            if symbol not in all_grids:
                logger.error(f"No grid found for {symbol}")
                return False
            
            grid_data = all_grids[symbol]
            
            # Get current price if not provided
            if current_price is None:
                # Try to get current price from Pacifica client or market data
                logger.info(f"No price provided for {symbol}, using default repair strategy")
                # For now, use a reasonable default based on recent AVAX prices
                current_price = 8.6  # Recent AVAX price from signal logs
                logger.info(f"Using estimated current price: ${current_price}")
            
            # Set the center price
            grid_data['center_price'] = current_price
            grid_data['initial_center'] = current_price
            grid_data['last_repair'] = datetime.utcnow().isoformat()
            
            logger.info(f"Fixed grid center for {symbol}: set to ${current_price}")
            return True
            
        except Exception as e:
            logger.error(f"Error fixing grid center for {symbol}: {e}")
            return False
    
    def remove_broken_grid(self, symbol: str) -> bool:
        """Remove a broken grid entirely."""
        if not self.grid_manager:
            logger.error("Grid manager not initialized")
            return False
        
        try:
            # Access internal grids
            all_grids = getattr(self.grid_manager, '_grids', {})
            
            if symbol not in all_grids:
                logger.error(f"No grid found for {symbol}")
                return False
            
            # Remove the grid
            del all_grids[symbol]
            
            # Also clean up metrics if they exist
            metrics = getattr(self.grid_manager, '_metrics', {})
            if symbol in metrics:
                del metrics[symbol]
            
            logger.info(f"Removed broken grid for {symbol}")
            return True
            
        except Exception as e:
            logger.error(f"Error removing grid for {symbol}: {e}")
            return False
    
    def print_diagnosis(self):
        """Print detailed diagnosis of broken grids."""
        print("\n" + "="*60)
        print("AVAX GRID REPAIR DIAGNOSIS")
        print("="*60)
        
        if not self.broken_grids:
            print("OK - No broken grids found")
            return
        
        print(f"❌ Found {len(self.broken_grids)} broken grid(s):")
        print()
        
        for i, grid in enumerate(self.broken_grids, 1):
            symbol = grid['symbol']
            print(f"{i}. Symbol: {symbol}")
            print(f"   State: {grid['state']}")
            print(f"   Issue: {grid['issue']}")
            print(f"   Created: {grid.get('created_at', 'Unknown')}")
            print(f"   Emergency Stop: {grid['emergency_stop']}")
            print()
            
            # Show available grid data
            grid_data = grid['grid_data']
            print(f"   Available data fields: {list(grid_data.keys())}")
            
            # Check for any price-related fields
            price_fields = [k for k in grid_data.keys() if 'price' in k.lower()]
            if price_fields:
                print(f"   Price-related fields: {price_fields}")
                for field in price_fields:
                    print(f"     {field}: {grid_data[field]}")
            print()
    
    def interactive_repair(self):
        """Interactive repair session."""
        if not self.broken_grids:
            print("No broken grids to repair!")
            return
        
        print("\nInteractive Repair Options:")
        print("1. Fix center price (recommended)")
        print("2. Remove broken grid")
        print("3. Skip")
        print()
        
        for grid in self.broken_grids:
            symbol = grid['symbol']
            print(f"Grid: {symbol}")
            choice = input("Choose action (1/2/3): ").strip()
            
            if choice == '1':
                # Fix center price
                price_input = input(f"Enter center price for {symbol} (leave empty for auto): ").strip()
                price = float(price_input) if price_input else None
                
                if self.fix_grid_center(symbol, price):
                    print(f"OK - Fixed {symbol} grid center")
                else:
                    print(f"❌ Failed to fix {symbol} grid")
                    
            elif choice == '2':
                # Remove grid
                confirm = input(f"Remove {symbol} grid? (yes/no): ").strip().lower()
                if confirm == 'yes':
                    if self.remove_broken_grid(symbol):
                        print(f"OK - Removed {symbol} grid")
                    else:
                        print(f"❌ Failed to remove {symbol} grid")
                        
            elif choice == '3':
                print(f"⏭️ Skipped {symbol}")
            
            print()


def main():
    parser = argparse.ArgumentParser(description='AVAX Grid Repair Tool')
    parser.add_argument('--check', action='store_true', help='Check for broken grids')
    parser.add_argument('--fix', action='store_true', help='Fix broken grids automatically')
    parser.add_argument('--remove', action='store_true', help='Remove broken grids')
    parser.add_argument('--symbol', default='AVAX', help='Symbol to check (default: AVAX)')
    parser.add_argument('--price', type=float, help='Center price to use for fixing')
    
    args = parser.parse_args()
    
    # Initialize repair tool
    repair = AVAXGridRepair()
    
    if not repair.initialize():
        print("❌ Failed to initialize repair tool")
        sys.exit(1)
    
    # Check for broken grids
    broken_grids = repair.check_broken_grids()
    
    # Print diagnosis
    repair.print_diagnosis()
    
    if not broken_grids:
        print("✅ No issues found!")
        return
    
    # Apply requested action
    if args.fix:
        print(f"\n🔧 Auto-fixing broken grids...")
        for grid in broken_grids:
            symbol = grid['symbol']
            if args.symbol == 'ALL' or symbol == args.symbol:
                success = repair.fix_grid_center(symbol, args.price)
                print(f"{'✅' if success else '❌'} {symbol}: {'Fixed' if success else 'Failed'}")
    
    elif args.remove:
        print(f"\n🗑️ Removing broken grids...")
        for grid in broken_grids:
            symbol = grid['symbol']
            if args.symbol == 'ALL' or symbol == args.symbol:
                success = repair.remove_broken_grid(symbol)
                print(f"{'✅' if success else '❌'} {symbol}: {'Removed' if success else 'Failed'}")
    
    else:
        # Interactive mode
        repair.interactive_repair()


if __name__ == '__main__':
    main()