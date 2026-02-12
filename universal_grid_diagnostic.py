#!/usr/bin/env python3
"""
Universal Grid State Diagnostic and Repair Tool
=============================================

Standalone tool for diagnosing and repairing grid state inconsistencies across ALL symbols.
This tool provides comprehensive analysis and automatic repair capabilities.

Features:
- Comprehensive grid state validation
- Automatic repair of inconsistent states
- Ticker format normalization
- Database synchronization
- Emergency cleanup capabilities
- Detailed reporting and statistics

Usage:
    python universal_grid_diagnostic.py [--auto-repair] [--symbols AVAX,BTC,ETH] [--emergency-cleanup]
    python universal_grid_diagnostic.py --report-only
    python universal_grid_diagnostic.py --emergency-cleanup

Author: Grid State Consistency System
Version: 1.0.0
"""

import argparse
import json
import sys
import os
from datetime import datetime
from typing import Dict, List, Any, Optional
import time

# Add path for importing trading bot modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'trading_bot_v2'))

try:
    from universal_grid_state_consistency import UniversalGridStateConsistencyManager
    from grid_lifecycle_manager import GridLifecycleManager
    from database import DatabaseManager
    from pacifica_client import PacificaClient
    from config import config
except ImportError as e:
    print(f"❌ Failed to import required modules: {e}")
    print("Make sure you're running this from the Bot3 directory")
    sys.exit(1)


class GridDiagnosticTool:
    """Comprehensive grid state diagnostic and repair tool."""
    
    def __init__(self):
        """Initialize the diagnostic tool."""
        self.consistency_manager: Optional[UniversalGridStateConsistencyManager] = None
        self.grid_manager: Optional[GridLifecycleManager] = None
        self.database_manager: Optional[DatabaseManager] = None
        self.client: Optional[PacificaClient] = None
        
        # Initialize components
        self._initialize_components()
    
    def _initialize_components(self):
        """Initialize all required components."""
        try:
            print("🔧 Initializing components...")
            
            # Initialize database
            self.database_manager = DatabaseManager()
            print("✅ Database manager initialized")
            
            # Initialize client
            self.client = PacificaClient()
            print("✅ Pacifica client initialized")
            
            # Initialize grid lifecycle manager
            self.grid_manager = GridLifecycleManager(
                client=self.client,
                risk_manager=None,  # Not needed for diagnostic
                db=self.database_manager,
                regime_detector=None  # Not needed for diagnostic
            )
            print("✅ Grid lifecycle manager initialized")
            
            # Initialize consistency manager
            self.consistency_manager = UniversalGridStateConsistencyManager(
                grid_lifecycle_manager=self.grid_manager,
                database_manager=self.database_manager,
                client=self.client
            )
            print("✅ Universal grid consistency manager initialized")
            
        except Exception as e:
            print(f"❌ Failed to initialize components: {e}")
            raise
    
    def run_full_diagnostic(self) -> Dict[str, Any]:
        """Run comprehensive grid state diagnostic."""
        print("\n" + "="*60)
        print("🔍 RUNNING COMPREHENSIVE GRID STATE DIAGNOSTIC")
        print("="*60)
        
        start_time = datetime.now()
        
        try:
            # Get current states
            print("\n📊 Analyzing current grid states...")
            memory_grids = self.consistency_manager._get_memory_grid_states()
            database_grids = self.consistency_manager._get_database_grid_states()
            
            print(f"Memory grids found: {len(memory_grids)}")
            print(f"Database grids found: {len(database_grids)}")
            
            # Display memory grids
            if memory_grids:
                print("\n🧠 MEMORY GRID STATES:")
                for symbol, state in memory_grids.items():
                    status = "✅ ACTIVE" if state.get('state') == 'active' else "⚠️ INACTIVE"
                    center = state.get('center_price', 'N/A')
                    capital = state.get('grid_capital', 0)
                    print(f"  {symbol}: {status} | Center: ${center} | Capital: ${capital}")
            
            # Display database grids
            if database_grids:
                print("\n💾 DATABASE GRID STATES:")
                for symbol, state in database_grids.items():
                    status = "✅ ACTIVE" if state.get('state') == 'active' else "⚠️ INACTIVE"
                    capital = state.get('grid_capital', 0)
                    print(f"  {symbol}: {status} | Capital: ${capital}")
            
            # Run validation
            print("\n🔍 Running consistency validation...")
            validation_report = self.consistency_manager.validate_and_repair_all_grids(automatic=False)
            
            # Display results
            print(f"\n📈 VALIDATION RESULTS:")
            print(f"  Total symbols: {validation_report.total_symbols}")
            print(f"  Consistent symbols: {validation_report.consistent_symbols}")
            print(f"  Inconsistent symbols: {validation_report.inconsistent_symbols}")
            print(f"  Orphaned memory grids: {len(validation_report.orphaned_memory_grids)}")
            print(f"  Orphaned database grids: {len(validation_report.orphaned_database_grids)}")
            
            # Display orphaned grids
            if validation_report.orphaned_memory_grids:
                print(f"\n👻 ORPHANED MEMORY GRIDS: {', '.join(validation_report.orphaned_memory_grids)}")
            
            if validation_report.orphaned_database_grids:
                print(f"\n👻 ORPHANED DATABASE GRIDS: {', '.join(validation_report.orphaned_database_grids)}")
            
            # Display repair results
            if validation_report.repair_results:
                print(f"\n🔧 REPAIR RESULTS:")
                for repair in validation_report.repair_results:
                    status = "✅ SUCCESS" if repair.success else "❌ FAILED"
                    issues = ', '.join([issue.value for issue in repair.issues_fixed])
                    print(f"  {repair.symbol}: {status} | Fixed: {issues}")
                    if not repair.success and repair.error_message:
                        print(f"    Error: {repair.error_message}")
            
            # Display recommendations
            if validation_report.recommendations:
                print(f"\n💡 RECOMMENDATIONS:")
                for i, rec in enumerate(validation_report.recommendations, 1):
                    print(f"  {i}. {rec}")
            
            # Get consistency statistics
            stats = self.consistency_manager.get_consistency_statistics()
            print(f"\n📊 CONSISTENCY STATISTICS:")
            print(f"  Total validations: {stats['total_validations']}")
            print(f"  Total repairs: {stats['total_repairs']}")
            print(f"  Successful repairs: {stats['successful_repairs']}")
            print(f"  Repair success rate: {stats['repair_success_rate']:.1f}%")
            
            elapsed = (datetime.now() - start_time).total_seconds()
            print(f"\n⏱️ Diagnostic completed in {elapsed:.2f} seconds")
            
            return {
                'validation_report': validation_report,
                'consistency_statistics': stats,
                'memory_grids': memory_grids,
                'database_grids': database_grids,
                'execution_time': elapsed
            }
            
        except Exception as e:
            print(f"❌ Diagnostic failed: {e}")
            return {'error': str(e)}
    
    def run_auto_repair(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        """Run automatic repair for specified symbols or all."""
        print("\n" + "="*60)
        print("🔧 RUNNING AUTOMATIC GRID REPAIR")
        print("="*60)
        
        start_time = datetime.now()
        
        try:
            if symbols:
                print(f"🎯 Targeting specific symbols: {', '.join(symbols)}")
            else:
                print("🎯 Targeting ALL symbols")
            
            # Force repair
            repair_result = self.consistency_manager.force_full_repair(symbols)
            
            # Display results
            validation_report = repair_result['validation_report']
            
            print(f"\n📈 REPAIR RESULTS:")
            print(f"  Total repairs attempted: {repair_result['total_repairs_attempted']}")
            print(f"  Successful repairs: {repair_result['successful_repairs']}")
            print(f"  Failed repairs: {repair_result['failed_repairs']}")
            
            if validation_report.repair_results:
                print(f"\n🔧 DETAILED REPAIR RESULTS:")
                for repair in validation_report.repair_results:
                    status = "✅ SUCCESS" if repair.success else "❌ FAILED"
                    actions = ', '.join(repair.repair_actions)
                    print(f"  {repair.symbol}: {status}")
                    print(f"    Actions: {actions}")
                    if not repair.success and repair.error_message:
                        print(f"    Error: {repair.error_message}")
            
            elapsed = (datetime.now() - start_time).total_seconds()
            print(f"\n⏱️ Auto-repair completed in {elapsed:.2f} seconds")
            
            return repair_result
            
        except Exception as e:
            print(f"❌ Auto-repair failed: {e}")
            return {'error': str(e)}
    
    def run_emergency_cleanup(self) -> Dict[str, Any]:
        """Run emergency cleanup of all grid states."""
        print("\n" + "="*60)
        print("🚨 RUNNING EMERGENCY GRID CLEANUP")
        print("="*60)
        print("⚠️ WARNING: This will reset ALL grid states!")
        print("⚠️ This is a drastic measure - use only when necessary!")
        
        # Ask for confirmation
        response = input("\nType 'EMERGENCY' to confirm cleanup: ")
        if response != 'EMERGENCY':
            print("❌ Emergency cleanup cancelled")
            return {'cancelled': True}
        
        start_time = datetime.now()
        
        try:
            # Run emergency cleanup
            cleanup_result = self.consistency_manager.emergency_grid_cleanup()
            
            # Display results
            print(f"\n🗑️ EMERGENCY CLEANUP RESULTS:")
            print(f"  Memory grids cleared: {cleanup_result['memory_cleared']}")
            print(f"  Database records cleared: {cleanup_result['database_cleared']}")
            
            if cleanup_result['errors']:
                print(f"\n❌ ERRORS:")
                for error in cleanup_result['errors']:
                    print(f"  - {error}")
            
            if cleanup_result['warnings']:
                print(f"\n⚠️ WARNINGS:")
                for warning in cleanup_result['warnings']:
                    print(f"  - {warning}")
            
            elapsed = (datetime.now() - start_time).total_seconds()
            print(f"\n⏱️ Emergency cleanup completed in {elapsed:.2f} seconds")
            
            return cleanup_result
            
        except Exception as e:
            print(f"❌ Emergency cleanup failed: {e}")
            return {'error': str(e)}
    
    def export_report(self, results: Dict[str, Any], filename: str = None) -> str:
        """Export diagnostic results to JSON file."""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"grid_diagnostic_report_{timestamp}.json"
        
        try:
            # Convert datetime objects to strings for JSON serialization
            def json_serializer(obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
            
            with open(filename, 'w') as f:
                json.dump(results, f, default=json_serializer, indent=2)
            
            print(f"\n📄 Report exported to: {filename}")
            return filename
            
        except Exception as e:
            print(f"❌ Failed to export report: {e}")
            return None


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Universal Grid State Diagnostic and Repair Tool")
    
    parser.add_argument('--auto-repair', action='store_true',
                       help='Run automatic repair for all issues found')
    parser.add_argument('--symbols', type=str,
                       help='Comma-separated list of symbols to target (e.g., AVAX,BTC,ETH)')
    parser.add_argument('--emergency-cleanup', action='store_true',
                       help='Run emergency cleanup (DESTRUCTIVE - resets all grid states)')
    parser.add_argument('--report-only', action='store_true',
                       help='Run diagnostic only, no repairs')
    parser.add_argument('--export', type=str,
                       help='Export results to specified JSON file')
    parser.add_argument('--continuous', action='store_true',
                       help='Run continuous monitoring (Ctrl+C to stop)')
    
    args = parser.parse_args()
    
    # Initialize tool
    try:
        tool = GridDiagnosticTool()
    except Exception as e:
        print(f"❌ Failed to initialize diagnostic tool: {e}")
        sys.exit(1)
    
    try:
        if args.emergency_cleanup:
            # Emergency cleanup mode
            result = tool.run_emergency_cleanup()
        
        elif args.continuous:
            # Continuous monitoring mode
            print("\n🔄 Starting continuous grid state monitoring...")
            print("Press Ctrl+C to stop")
            
            try:
                while True:
                    print(f"\n{'='*60}")
                    print(f"🕐 Monitoring check at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    
                    result = tool.run_full_diagnostic()
                    
                    print("\n💤 Sleeping for 5 minutes...")
                    time.sleep(300)  # 5 minutes
            
            except KeyboardInterrupt:
                print("\n🛑 Continuous monitoring stopped by user")
        
        else:
            # Standard diagnostic mode
            result = tool.run_full_diagnostic()
            
            # Auto-repair if requested
            if args.auto_repair:
                symbols = None
                if args.symbols:
                    symbols = [s.strip().upper() for s in args.symbols.split(',')]
                
                repair_result = tool.run_auto_repair(symbols)
                result['auto_repair'] = repair_result
            
            # Export results if requested
            if args.export or 'validation_report' in result:
                filename = args.export if args.export else None
                tool.export_report(result, filename)
    
    except KeyboardInterrupt:
        print("\n🛑 Diagnostic interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Diagnostic failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()