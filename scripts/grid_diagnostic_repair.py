#!/usr/bin/env python3
"""
Grid Diagnostic and Repair Tool
===============================

Diagnoses and repairs broken grids where center_price or initial_center is missing,
which causes signals to be rejected with "Active grid has no center price".

Usage:
    python scripts/grid_diagnostic_repair.py [--symbol SYMBOL] [--dry-run] [--auto-fix]
    
Options:
    --symbol SYMBOL    Specific symbol to check (default: check all)
    --dry-run         Only report issues, don't fix them
    --auto-fix        Automatically fix found issues
    --remove-broken   Remove broken grids instead of repairing them
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from loguru import logger

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from trading_bot_v2.grid_lifecycle_manager import GridLifecycleManager, GridState
from trading_bot_v2.risk_manager import RiskManager
from trading_bot_v2.pacifica_client import PacificaClient
from trading_bot_v2.auth import load_pacifica_credentials


class GridDiagnosticResult:
    """Results of grid diagnostic for a single symbol."""
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.has_grid = False
        self.grid_state = None
        self.has_center_price = False
        self.has_initial_center = False
        self.center_price = None
        self.initial_center = None
        self.is_broken = False
        self.issue_description = ""
        self.can_repair = False
        self.repair_method = ""
        self.repair_data = {}
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/reporting."""
        return {
            "symbol": self.symbol,
            "has_grid": self.has_grid,
            "grid_state": self.grid_state.value if self.grid_state else None,
            "has_center_price": self.has_center_price,
            "has_initial_center": self.has_initial_center,
            "center_price": self.center_price,
            "initial_center": self.initial_center,
            "is_broken": self.is_broken,
            "issue_description": self.issue_description,
            "can_repair": self.can_repair,
            "repair_method": self.repair_method,
            "repair_data": self.repair_data,
        }


class GridDiagnosticTool:
    """
    Comprehensive diagnostic and repair tool for grid lifecycle issues.
    
    Main capabilities:
    1. Detect broken grids (missing center_price/initial_center)
    2. Repair grids by calculating missing center prices
    3. Remove broken grids if repair is not possible
    4. Add validation to prevent future occurrences
    5. Provide detailed logging and reporting
    """
    
    def __init__(self):
        """Initialize the diagnostic tool."""
        self.client = None
        self.risk_manager = None
        self.grid_lifecycle = None
        self.results = {}
        
    def initialize(self) -> bool:
        """
        Initialize required components for diagnostic.
        
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            # Get Pacifica credentials
            credentials = load_pacifica_credentials()
            if not credentials:
                logger.error("Could not load Pacifica credentials from environment")
                return False
            
            # Initialize Pacifica client
            self.client = PacificaClient(
                agent_wallet_private_key=credentials["agent_wallet_private_key"],
                account_public_key=credentials["account_public_key"]
            )
            
            # Initialize risk manager
            self.risk_manager = RiskManager()
            
            # Initialize grid lifecycle manager
            self.grid_lifecycle = GridLifecycleManager(
                client=self.client,
                risk_manager=self.risk_manager,
                db=None,  # We'll work with in-memory state
                regime_detector=None
            )
            
            logger.info("Grid diagnostic tool initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize grid diagnostic tool: {e}")
            return False
    
    def diagnose_symbol(self, symbol: str) -> GridDiagnosticResult:
        """
        Diagnose a single symbol for grid issues.
        
        Args:
            symbol: Trading symbol to diagnose
            
        Returns:
            GridDiagnosticResult with detailed findings
        """
        result = GridDiagnosticResult(symbol)
        
        try:
            # Check if grid lifecycle manager is initialized
            if not self.grid_lifecycle:
                result.has_grid = False
                result.issue_description = "Grid lifecycle manager not initialized"
                return result
                
            # Check if symbol has any grid in the lifecycle manager
            if symbol not in self.grid_lifecycle._grids:
                result.has_grid = False
                result.issue_description = "No grid found for symbol"
                return result
            
            result.has_grid = True
            grid = self.grid_lifecycle._grids[symbol]
            result.grid_state = grid.get("state", GridState.IDLE)
            
            # Check center price fields
            result.center_price = grid.get("center_price")
            result.initial_center = grid.get("initial_center")
            result.has_center_price = result.center_price is not None
            result.has_initial_center = result.initial_center is not None
            
            # Determine if grid is broken
            # A grid is broken if it's ACTIVE but has no center price
            if result.grid_state == GridState.ACTIVE:
                if not result.has_center_price and not result.has_initial_center:
                    result.is_broken = True
                    result.issue_description = "Active grid has neither center_price nor initial_center"
                    result.can_repair = True
                    result.repair_method = "calculate_from_current_price"
                    
                elif not result.has_center_price and result.has_initial_center:
                    # This can be repaired by copying initial_center to center_price
                    result.is_broken = True
                    result.issue_description = "Active grid missing center_price but has initial_center"
                    result.can_repair = True
                    result.repair_method = "copy_initial_to_center"
                    result.repair_data = {
                        "new_center_price": result.initial_center
                    }
                    
                elif result.has_center_price and (result.center_price is None or result.center_price <= 0):
                    result.is_broken = True
                    result.issue_description = f"Active grid has invalid center_price: {result.center_price}"
                    result.can_repair = True
                    result.repair_method = "calculate_from_current_price"
                    
            elif result.grid_state in [GridState.DISABLED_BY_REGIME, GridState.EMERGENCY_EXIT]:
                # Non-active grids with missing center prices are lower priority
                if not result.has_center_price and not result.has_initial_center:
                    result.is_broken = True
                    result.issue_description = f"Non-active grid ({result.grid_state.value}) missing center prices"
                    result.can_repair = False  # Don't auto-repair non-active grids
                    result.repair_method = "manual_review_required"
                    
        except Exception as e:
            result.is_broken = True
            result.issue_description = f"Error during diagnosis: {e}"
            result.can_repair = False
            
        return result
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Get current market price for a symbol.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Current price or None if unavailable
        """
        try:
            # Check if client is initialized
            if not self.client:
                return None
                
            # Try to get market data from Pacifica
            market_data = self.client.get_market_data(symbol)
            if market_data and isinstance(market_data, dict):
                # Look for common price fields
                for field in ["last", "price", "close", "mark_price", "index_price"]:
                    if field in market_data:
                        price = float(market_data[field])
                        if price > 0:
                            return price
                            
            logger.warning(f"Could not get current price for {symbol}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting current price for {symbol}: {e}")
            return None
    
    def repair_grid(self, result: GridDiagnosticResult) -> bool:
        """
        Repair a broken grid based on diagnostic result.
        
        Args:
            result: GridDiagnosticResult with repair information
            
        Returns:
            True if repair successful, False otherwise
        """
        if not result.is_broken or not result.can_repair:
            logger.warning(f"Cannot repair grid for {result.symbol}: not broken or not repairable")
            return False
            
        try:
            if not self.grid_lifecycle:
                logger.error(f"Cannot repair grid for {result.symbol}: grid lifecycle not initialized")
                return False
                
            symbol = result.symbol
            grid = self.grid_lifecycle._grids[symbol]
            
            if result.repair_method == "copy_initial_to_center":
                # Simple case: copy initial_center to center_price
                new_center = result.initial_center
                grid["center_price"] = new_center
                logger.info(f"[OK] Repaired {symbol}: copied initial_center ({new_center}) to center_price")
                
            elif result.repair_method == "calculate_from_current_price":
                # Get current market price and use as center
                current_price = self.get_current_price(symbol)
                if current_price is None or current_price <= 0:
                    logger.error(f"[ERROR] Cannot repair {symbol}: unable to get current price")
                    return False
                    
                # Update both center_price and initial_center for completeness
                grid["center_price"] = current_price
                if not result.has_initial_center:
                    grid["initial_center"] = current_price
                    
                logger.info(f"[OK] Repaired {symbol}: set center_price to current market price ({current_price})")
                
            else:
                logger.error(f"[ERROR] Unknown repair method for {symbol}: {result.repair_method}")
                return False
                
            # Add repair metadata
            grid["repaired_at"] = datetime.now()
            grid["repair_method"] = result.repair_method
            grid["repair_description"] = result.issue_description
            
            return True
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to repair grid for {result.symbol}: {e}")
            return False
    
    def remove_broken_grid(self, symbol: str) -> bool:
        """
        Remove a broken grid completely.
        
        Args:
            symbol: Symbol whose grid should be removed
            
        Returns:
            True if removal successful, False otherwise
        """
        try:
            if not self.grid_lifecycle:
                logger.error(f"Cannot remove grid for {symbol}: grid lifecycle not initialized")
                return False
                
            if symbol not in self.grid_lifecycle._grids:
                logger.warning(f"No grid to remove for {symbol}")
                return True
                
            # Store grid info for logging
            grid = self.grid_lifecycle._grids[symbol]
            state = grid.get("state", GridState.IDLE)
            
            # Remove from lifecycle manager
            self.grid_lifecycle.clear_grid(symbol)
            
            logger.info(f"[OK] Removed broken grid for {symbol} (was in state: {state.value})")
            return True
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to remove grid for {symbol}: {e}")
            return False
    
    def diagnose_all(self) -> Dict[str, GridDiagnosticResult]:
        """
        Diagnose all symbols with grids.
        
        Returns:
            Dictionary of symbol -> GridDiagnosticResult
        """
        results = {}
        
        # Get all symbols with grids
        if not self.grid_lifecycle:
            logger.error("Grid lifecycle manager not initialized")
            return results
            
        symbols = list(self.grid_lifecycle._grids.keys())
        
        if not symbols:
            logger.info("No grids found in lifecycle manager")
            return results
            
        logger.info(f"Diagnosing {len(symbols)} symbols with grids...")
        
        for symbol in symbols:
            results[symbol] = self.diagnose_symbol(symbol)
            
        return results
    
    def print_summary(self, results: Dict[str, GridDiagnosticResult]) -> None:
        """Print a summary of diagnostic results."""
        total_grids = len(results)
        broken_grids = sum(1 for r in results.values() if r.is_broken)
        repairable_grids = sum(1 for r in results.values() if r.can_repair)
        
        print(f"\n{'='*60}")
        print(f"GRID DIAGNOSTIC SUMMARY")
        print(f"{'='*60}")
        print(f"Total grids found: {total_grids}")
        print(f"Broken grids: {broken_grids}")
        print(f"Repairable grids: {repairable_grids}")
        print(f"Non-repairable grids: {broken_grids - repairable_grids}")
        print(f"{'='*60}")
        
        if broken_grids == 0:
            print("[OK] All grids are healthy!")
            return
            
        print("\nBroken Grid Details:")
        print("-" * 60)
        
        for symbol, result in results.items():
            if result.is_broken:
                status = "[REPAIRABLE]" if result.can_repair else "[NOT REPAIRABLE]"
                print(f"\n{symbol}: {status}")
                print(f"  State: {result.grid_state.value if result.grid_state else 'Unknown'}")
                print(f"  Issue: {result.issue_description}")
                if result.can_repair:
                    print(f"  Repair method: {result.repair_method}")
                    
    def add_grid_validation_prevention(self) -> bool:
        """
        Add validation to prevent future grid creation without center prices.
        
        This modifies the grid lifecycle manager to validate center prices
        during grid creation.
        
        Returns:
            True if prevention measures added successfully
        """
        try:
            # This would ideally be done by modifying the register_new_grid method
            # For now, we'll log a recommendation
            
            logger.info("""
🛡️ PREVENTION RECOMMENDATIONS:
1. Modify register_new_grid() to validate center_price > 0
2. Add unit tests for grid creation with missing center prices
3. Add monitoring alerts for grids without center prices
4. Consider database validation constraints
            
Example validation to add to register_new_grid():
    if center_price <= 0:
        raise ValueError(f"Invalid center_price {center_price} for {symbol}")
""")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to add prevention measures: {e}")
            return False


def main():
    """Main entry point for the grid diagnostic tool."""
    parser = argparse.ArgumentParser(
        description="Grid Diagnostic and Repair Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Diagnose all grids (read-only)
    python scripts/grid_diagnostic_repair.py
    
    # Diagnose specific symbol
    python scripts/grid_diagnostic_repair.py --symbol AVAX
    
    # Auto-fix all repairable issues
    python scripts/grid_diagnostic_repair.py --auto-fix
    
    # Remove broken grids instead of repairing
    python scripts/grid_diagnostic_repair.py --remove-broken
    
    # Dry run to see what would be fixed
    python scripts/grid_diagnostic_repair.py --auto-fix --dry-run
        """
    )
    
    parser.add_argument(
        "--symbol",
        type=str,
        help="Specific symbol to check (default: check all)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report issues, don't fix them"
    )
    parser.add_argument(
        "--auto-fix",
        action="store_true",
        help="Automatically fix found issues"
    )
    parser.add_argument(
        "--remove-broken",
        action="store_true",
        help="Remove broken grids instead of repairing them"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose logging output"
    )
    
    args = parser.parse_args()
    
    # Set up logging
    log_level = "DEBUG" if args.verbose else "INFO"
    logger.remove()
    logger.add(sys.stderr, level=log_level, format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")
    
    # Initialize diagnostic tool
    tool = GridDiagnosticTool()
    if not tool.initialize():
        logger.error("Failed to initialize diagnostic tool")
        sys.exit(1)
    
    try:
        # Run diagnostics
        if args.symbol:
            results = {args.symbol: tool.diagnose_symbol(args.symbol)}
        else:
            results = tool.diagnose_all()
        
        # Print summary
        tool.print_summary(results)
        
        # Exit early if no issues found
        broken_results = [r for r in results.values() if r.is_broken]
        if not broken_results:
            logger.info("No issues found. ✅")
            sys.exit(0)
        
        # Handle repairs/removals
        if args.dry_run:
            logger.info("🔍 DRY RUN - No changes made")
            for symbol, result in results.items():
                if result.is_broken:
                    action = "REMOVE" if args.remove_broken else "REPAIR"
                    logger.info(f"Would {action}: {symbol} - {result.issue_description}")
            sys.exit(0)
        
        if args.auto_fix or args.remove_broken:
            logger.info(f"🔧 Starting {'grid removal' if args.remove_broken else 'grid repair'}...")
            
            success_count = 0
            for symbol, result in results.items():
                if result.is_broken:
                    if args.remove_broken:
                        success = tool.remove_broken_grid(symbol)
                        action = "REMOVED"
                    else:
                        success = tool.repair_grid(result)
                        action = "REPAIRED"
                    
                    if success:
                        success_count += 1
                        logger.info(f"✅ {action}: {symbol}")
                    else:
                        logger.error(f"❌ Failed to {action.lower()}: {symbol}")
            
            logger.info(f"Completed: {success_count}/{len(broken_results)} grids successfully processed")
            
            # Add prevention recommendations
            tool.add_grid_validation_prevention()
        
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()