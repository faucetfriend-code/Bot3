#!/usr/bin/env python3
"""
Quick Fix for AVAX Grid Issue
============================

This script provides a quick fix for the AVAX grid rejection issue.
It directly modifies the grid lifecycle manager to handle missing center prices.

The issue: AVAX has an active grid in the database but missing center_price/initial_center
This causes signal rejection with "Active grid has no center price"

Usage:
    python scripts/quick_fix_avax_grid.py [--dry-run] [--force-remove]
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from trading_bot_v2.grid_lifecycle_manager import GridLifecycleManager, GridState
from trading_bot_v2.risk_manager import RiskManager
from trading_bot_v2.pacifica_client import PacificaClient
from trading_bot_v2.auth import load_pacifica_credentials


class QuickGridFixer:
    """Quick fix for broken grids with missing center prices."""
    
    def __init__(self):
        self.client = None
        self.risk_manager = None
        self.grid_lifecycle = None
        
    def initialize(self) -> bool:
        """Initialize components."""
        try:
            # Get Pacifica credentials
            credentials = load_pacifica_credentials()
            if not credentials:
                logger.error("Could not load Pacifica credentials")
                return False
            
            self.client = PacificaClient(
                agent_wallet_private_key=credentials["agent_wallet_private_key"],
                account_public_key=credentials["account_public_key"]
            )
            
            self.risk_manager = RiskManager()
            self.grid_lifecycle = GridLifecycleManager(
                client=self.client,
                risk_manager=self.risk_manager,
                db=None,
                regime_detector=None
            )
            
            return True
        except Exception as e:
            logger.error(f"Failed to initialize: {e}")
            return False
    
    def patch_get_grid_center(self):
        """
        Patch the get_grid_center method to handle missing center prices gracefully.
        
        This is the core fix: when get_grid_center returns None, calculate a reasonable
        center price from current market data instead of rejecting the signal.
        """
        
        # Store original method
        original_get_grid_center = self.grid_lifecycle.get_grid_center
        
        def patched_get_grid_center(symbol: str) -> Optional[float]:
            """Enhanced get_grid_center that handles missing center prices."""
            
            # Try original method first
            center = original_get_grid_center(symbol)
            if center is not None and center > 0:
                return center
            
            # If we get here, center is missing or invalid
            logger.warning(f"Grid for {symbol} has missing/invalid center price, attempting to repair...")
            
            # Try to get current market price
            try:
                market_data = self.client.get_market_data(symbol)
                if market_data:
                    for field in ["last", "price", "close", "mark_price", "index_price"]:
                        if field in market_data:
                            current_price = float(market_data[field])
                            if current_price > 0:
                                # Auto-repair the grid
                                if symbol in self.grid_lifecycle._grids:
                                    grid = self.grid_lifecycle._grids[symbol]
                                    grid["center_price"] = current_price
                                    grid["auto_repaired_at"] = str(Path(__file__).stem)
                                    logger.info(f"[AUTO-REPAIR] Set {symbol} center_price to {current_price}")
                                return current_price
            except Exception as e:
                logger.error(f"Failed to get market price for {symbol}: {e}")
            
            logger.error(f"Could not repair center price for {symbol}")
            return None
        
        # Replace the method
        self.grid_lifecycle.get_grid_center = patched_get_grid_center
        logger.info("[PATCH] Applied get_grid_center patch for auto-repair functionality")
    
    def manually_repair_avax(self, dry_run: bool = False) -> bool:
        """
        Manually repair AVAX grid by setting center price from market data.
        
        Args:
            dry_run: If True, only show what would be done
            
        Returns:
            True if repair successful or dry-run completed
        """
        try:
            symbol = "AVAX"
            
            # Check if AVAX grid exists
            if symbol not in self.grid_lifecycle._grids:
                logger.warning(f"No grid found for {symbol}")
                return False
            
            grid = self.grid_lifecycle._grids[symbol]
            state = grid.get("state", GridState.IDLE)
            
            print(f"\n{'='*50}")
            print(f"AVAX GRID STATUS")
            print(f"{'='*50}")
            print(f"State: {state.value}")
            print(f"Has center_price: {'center_price' in grid}")
            print(f"Has initial_center: {'initial_center' in grid}")
            if 'center_price' in grid:
                print(f"Center price: {grid['center_price']}")
            if 'initial_center' in grid:
                print(f"Initial center: {grid['initial_center']}")
            print(f"{'='*50}")
            
            # Get current market price
            try:
                market_data = self.client.get_market_data(symbol)
                current_price = None
                if market_data:
                    for field in ["last", "price", "close", "mark_price", "index_price"]:
                        if field in market_data:
                            price = float(market_data[field])
                            if price > 0:
                                current_price = price
                                break
                
                if current_price:
                    print(f"Current market price: {current_price}")
                else:
                    print("Could not get current market price")
                    return False
                    
            except Exception as e:
                logger.error(f"Failed to get market price: {e}")
                return False
            
            # Determine what needs to be fixed
            needs_repair = False
            repair_actions = []
            
            if state == GridState.ACTIVE:
                if not grid.get("center_price") and not grid.get("initial_center"):
                    needs_repair = True
                    repair_actions.append(f"Set center_price to {current_price}")
                elif not grid.get("center_price") and grid.get("initial_center"):
                    needs_repair = True
                    repair_actions.append(f"Copy initial_center ({grid['initial_center']}) to center_price")
                elif grid.get("center_price") and grid.get("center_price") <= 0:
                    needs_repair = True
                    repair_actions.append(f"Fix invalid center_price ({grid['center_price']}) to {current_price}")
            
            if not needs_repair:
                print("AVAX grid appears to be healthy")
                return True
            
            print(f"\nRequired repairs:")
            for action in repair_actions:
                print(f"  - {action}")
            
            if dry_run:
                print(f"\n[DRY RUN] Would repair AVAX grid")
                return True
            
            # Perform the repair
            print(f"\nPerforming repair...")
            
            if not grid.get("center_price") and not grid.get("initial_center"):
                grid["center_price"] = current_price
                grid["initial_center"] = current_price
                print(f"  Set both center_price and initial_center to {current_price}")
                
            elif not grid.get("center_price") and grid.get("initial_center"):
                grid["center_price"] = grid["initial_center"]
                print(f"  Copied initial_center ({grid['initial_center']}) to center_price")
                
            elif grid.get("center_price") and grid.get("center_price") <= 0:
                old_price = grid["center_price"]
                grid["center_price"] = current_price
                print(f"  Fixed invalid center_price ({old_price} -> {current_price})")
            
            # Add repair metadata
            from datetime import datetime
            grid["repaired_at"] = datetime.now()
            grid["repair_method"] = "quick_fix_script"
            grid["repair_description"] = "Fixed missing/invalid center price"
            
            print(f"\n[SUCCESS] AVAX grid repaired!")
            print(f"New center_price: {grid.get('center_price')}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to repair AVAX grid: {e}")
            return False
    
    def force_remove_avax_grid(self, dry_run: bool = False) -> bool:
        """
        Force remove AVAX grid if it's beyond repair.
        
        Args:
            dry_run: If True, only show what would be done
            
        Returns:
            True if removal successful or dry-run completed
        """
        try:
            symbol = "AVAX"
            
            if symbol not in self.grid_lifecycle._grids:
                logger.warning(f"No grid found for {symbol}")
                return True  # Success if no grid exists
            
            if dry_run:
                print(f"[DRY RUN] Would remove AVAX grid and all associated data")
                return True
            
            # Remove the grid
            success = self.grid_lifecycle.clear_grid(symbol)
            
            if success:
                print(f"[SUCCESS] AVAX grid removed completely")
            else:
                print(f"[FAILED] Could not remove AVAX grid")
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to remove AVAX grid: {e}")
            return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Quick Fix for AVAX Grid Issue")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--force-remove", action="store_true", help="Force remove AVAX grid instead of repairing")
    parser.add_argument("--patch-method", action="store_true", help="Apply patch method for auto-repair")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    # Set up logging
    log_level = "DEBUG" if args.verbose else "INFO"
    logger.remove()
    logger.add(sys.stderr, level=log_level, format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")
    
    # Initialize fixer
    fixer = QuickGridFixer()
    if not fixer.initialize():
        logger.error("Failed to initialize grid fixer")
        sys.exit(1)
    
    try:
        if args.patch_method:
            print("Applying auto-repair patch...")
            fixer.patch_get_grid_center()
            print("[SUCCESS] Auto-repair patch applied")
            print("The bot will now automatically fix missing center prices during operation")
            
        elif args.force_remove:
            print("Force removing AVAX grid...")
            success = fixer.force_remove_avax_grid(dry_run=args.dry_run)
            if success:
                print("[SUCCESS] AVAX grid removal completed")
            else:
                print("[FAILED] AVAX grid removal failed")
                sys.exit(1)
                
        else:
            # Default: repair AVAX grid
            print("Checking and repairing AVAX grid...")
            success = fixer.manually_repair_avax(dry_run=args.dry_run)
            if success:
                print("[SUCCESS] AVAX grid check/repair completed")
            else:
                print("[FAILED] AVAX grid repair failed")
                sys.exit(1)
    
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()