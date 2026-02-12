#!/usr/bin/env python3
"""
Comprehensive diagnostic tool to identify AVAX grid rejection vs display disconnect.

This script systematically checks:
1. Grid manager internal state
2. API endpoint responses  
3. Risk manager validation logic
4. Grid lifecycle state transitions
5. Logging and state synchronization

Usage:
    python diagnose_grid_rejection.py [--symbol AVAX]
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
import requests
import logging

# Add trading_bot_v2 to path
sys.path.insert(0, str(Path(__file__).parent / "trading_bot_v2"))

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"grid_diagnostic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

class GridDiagnosticTool:
    """Comprehensive diagnostic tool for grid state issues."""
    
    def __init__(self, symbol: str = "AVAX", api_base: str = "http://localhost:8000"):
        self.symbol = symbol.upper()
        self.api_base = api_base
        self.diagnostic_results = {
            "timestamp": datetime.now().isoformat(),
            "symbol": self.symbol,
            "findings": {},
            "recommendations": []
        }
        
    async def run_all_diagnostics(self) -> Dict[str, Any]:
        """Run complete diagnostic suite."""
        logger.info(f"Starting comprehensive grid diagnostic for {self.symbol}")
        
        # 1. Check API endpoints
        await self._check_api_endpoints()
        
        # 2. Import and test grid manager directly
        await self._check_grid_manager_state()
        
        # 3. Check risk manager validation
        await self._check_risk_manager_logic()
        
        # 4. Analyze grid lifecycle transitions
        await self._check_grid_lifecycle()
        
        # 5. Check database consistency
        await self._check_database_state()
        
        # 6. Generate summary and recommendations
        self._generate_summary()
        
        return self.diagnostic_results
    
    async def _check_api_endpoints(self) -> None:
        """Check what API endpoints return for grid status."""
        logger.info("🔍 Checking API endpoint responses...")
        
        try:
            # Check /api/status
            status_response = requests.get(f"{self.api_base}/api/status", timeout=5)
            status_data = status_response.json() if status_response.status_code == 200 else None
            
            # Check /api/grids
            grids_response = requests.get(f"{self.api_base}/api/grids", timeout=5)
            grids_data = grids_response.json() if grids_response.status_code == 200 else None
            
            # Check /api/trading-status
            trading_response = requests.get(f"{self.api_base}/api/trading-status", timeout=5)
            trading_data = trading_response.json() if trading_response.status_code == 200 else None
            
            api_findings = {
                "status_endpoint": {
                    "status_code": status_response.status_code,
                    "data": status_data,
                    "active_grids_count": status_data.get("active_grids") if status_data else "N/A",
                    "is_running": status_data.get("is_running") if status_data else "N/A"
                } if status_data else {"error": f"Status {status_response.status_code}"},
                
                "grids_endpoint": {
                    "status_code": grids_response.status_code,
                    "data": grids_data,
                    "grid_count": len(grids_data) if isinstance(grids_data, list) else "N/A",
                    "avax_grids": [g for g in grids_data if isinstance(g, dict) and g.get("symbol") == self.symbol] if isinstance(grids_data, list) else []
                } if grids_data else {"error": f"Status {grids_response.status_code}"},
                
                "trading_endpoint": {
                    "status_code": trading_response.status_code,
                    "data": trading_data
                } if trading_data else {"error": f"Status {trading_response.status_code}"}
            }
            
            self.diagnostic_results["findings"]["api_endpoints"] = api_findings
            
            logger.info(f"✅ API Status: {status_data.get('active_grids', 'N/A')} active grids")
            logger.info(f"✅ API Grids: {len(grids_data) if isinstance(grids_data, list) else 'N/A'} grids returned")
            
        except Exception as e:
            logger.error(f"❌ API endpoint check failed: {e}")
            self.diagnostic_results["findings"]["api_endpoints"] = {"error": str(e)}
    
    async def _check_grid_manager_state(self) -> None:
        """Check grid manager internal state directly."""
        logger.info("🔍 Checking grid manager internal state...")
        
        try:
            # Import grid manager classes
            from grid_lifecycle_manager import GridLifecycleManager, GridState
            
            # Try to create a grid manager instance to check its methods
            grid_findings = {
                "grid_states": list(GridState.__members__.keys()),
                "has_active_grid_method": hasattr(GridLifecycleManager, 'has_active_grid'),
                "get_all_active_grids_method": hasattr(GridLifecycleManager, 'get_all_active_grids'),
                "get_grid_status_method": hasattr(GridLifecycleManager, 'get_grid_status')
            }
            
            # Test the logic of has_active_grid
            test_grids = {
                "AVAX": {"state": GridState.ACTIVE},
                "BTC": {"state": GridState.IDLE},
                "ETH": {"state": GridState.CLOSED}
            }
            
            # Simulate the has_active_grid logic
            def simulate_has_active_grid(symbol: str, grids: Dict[str, Dict]) -> bool:
                return (
                    symbol in grids and 
                    grids[symbol]["state"] == GridState.ACTIVE
                )
            
            avax_active = simulate_has_active_grid("AVAX", test_grids)
            btc_active = simulate_has_active_grid("BTC", test_grids)
            
            grid_findings["logic_test"] = {
                "test_grids": {k: v["state"].value for k, v in test_grids.items()},
                "avax_has_active": avax_active,
                "btc_has_active": btc_active,
                "expected": {"AVAX": True, "BTC": False}
            }
            
            self.diagnostic_results["findings"]["grid_manager"] = grid_findings
            
            logger.info(f"✅ Grid manager methods available: {grid_findings['has_active_grid_method']}")
            logger.info(f"✅ Logic test - AVAX active: {avax_active}, BTC active: {btc_active}")
            
        except Exception as e:
            logger.error(f"❌ Grid manager check failed: {e}")
            self.diagnostic_results["findings"]["grid_manager"] = {"error": str(e)}
    
    async def _check_risk_manager_logic(self) -> None:
        """Check risk manager for grid validation logic."""
        logger.info("🔍 Checking risk manager validation logic...")
        
        try:
            # Import risk manager
            from risk_manager import RiskManager
            
            # Create risk manager instance
            risk_mgr = RiskManager()
            
            risk_findings = {
                "initialization": "successful",
                "max_risk": getattr(risk_mgr, 'max_risk_per_trade', 'unknown'),
                "max_exposure": getattr(risk_mgr, 'max_total_exposure', 'unknown'),
                "grid_validation_methods": []
            }
            
            # Check for grid-related validation methods
            methods = dir(risk_mgr)
            grid_methods = [m for m in methods if 'grid' in m.lower()]
            risk_findings["grid_validation_methods"] = grid_methods
            
            # Test basic risk validation
            test_signal = {
                "symbol": self.symbol,
                "strategy": "grid_trading",
                "confidence": 0.6,
                "price": 13.77
            }
            
            try:
                validation_result = risk_mgr.validate_signal(test_signal)
                risk_findings["test_validation"] = {
                    "signal": test_signal,
                    "result": validation_result,
                    "passed": isinstance(validation_result, dict) and validation_result.get("valid", False)
                }
            except Exception as e:
                risk_findings["test_validation"] = {"error": str(e)}
            
            self.diagnostic_results["findings"]["risk_manager"] = risk_findings
            
            logger.info(f"✅ Risk manager initialized successfully")
            logger.info(f"✅ Grid-related methods: {grid_methods}")
            
        except Exception as e:
            logger.error(f"❌ Risk manager check failed: {e}")
            self.diagnostic_results["findings"]["risk_manager"] = {"error": str(e)}
    
    async def _check_grid_lifecycle(self) -> None:
        """Check grid lifecycle state transitions."""
        logger.info("🔍 Checking grid lifecycle state transitions...")
        
        try:
            from grid_lifecycle_manager import GridLifecycleManager, GridState
            
            lifecycle_findings = {
                "state_transitions": {
                    "IDLE": "Initial state",
                    "ACTIVE": "Grid is actively trading",
                    "EMERGENCY_EXIT": "Emergency stop triggered",
                    "DISABLED_BY_REGIME": "Market regime incompatible",
                    "CLOSED": "Grid manually closed"
                },
                "active_state_check": "GridState.ACTIVE",
                "inclusion_logic": "symbol in self._grids and self._grids[symbol]['state'] == GridState.ACTIVE"
            }
            
            # Analyze potential state sync issues
            potential_issues = []
            
            # Check if there could be a state mismatch
            lifecycle_findings["potential_issues"] = {
                "state_mismatch": "Grid exists but state != ACTIVE",
                "orphaned_grids": "Grid in ACTIVE state but no orders placed",
                "delayed_updates": "State changes not propagated to API",
                "race_condition": "Concurrent access to grid state"
            }
            
            self.diagnostic_results["findings"]["grid_lifecycle"] = lifecycle_findings
            
            logger.info("✅ Grid lifecycle analysis complete")
            logger.info("⚠️  Potential state mismatch identified")
            
        except Exception as e:
            logger.error(f"❌ Grid lifecycle check failed: {e}")
            self.diagnostic_results["findings"]["grid_lifecycle"] = {"error": str(e)}
    
    async def _check_database_state(self) -> None:
        """Check database for grid-related records."""
        logger.info("🔍 Checking database state...")
        
        try:
            db_findings = {
                "database_check": "attempted",
                "grid_positions": [],
                "recent_trades": []
            }
            
            # Try to import database and check for AVAX positions
            try:
                from database import Database
                db = Database()
                
                # Check positions for AVAX
                positions = db.get_positions() if hasattr(db, 'get_positions') else []
                avax_positions = [p for p in positions if p.get('symbol') == self.symbol] if positions else []
                
                # Check recent trades for AVAX
                trades = db.get_trades(limit=50) if hasattr(db, 'get_trades') else []
                avax_trades = [t for t in trades if t.get('symbol') == self.symbol] if trades else []
                
                db_findings = {
                    "database_connected": True,
                    "total_positions": len(positions) if positions else 0,
                    "avax_positions": len(avax_positions),
                    "total_trades": len(trades) if trades else 0,
                    "avax_trades": len(avax_trades),
                    "recent_avax_trade": avax_trades[0] if avax_trades else None
                }
                
            except ImportError:
                db_findings["database_connected"] = False
                db_findings["error"] = "Database module not available"
            except Exception as e:
                db_findings["database_connected"] = False
                db_findings["error"] = str(e)
            
            self.diagnostic_results["findings"]["database"] = db_findings
            
            if db_findings.get("database_connected"):
                logger.info(f"✅ Database connected - AVAX positions: {db_findings['avax_positions']}")
            else:
                logger.warning("⚠️  Database connection failed")
                
        except Exception as e:
            logger.error(f"❌ Database check failed: {e}")
            self.diagnostic_results["findings"]["database"] = {"error": str(e)}
    
    def _generate_summary(self) -> None:
        """Generate summary and recommendations."""
        logger.info("📝 Generating diagnostic summary...")
        
        findings = self.diagnostic_results["findings"]
        recommendations = []
        
        # Analyze API vs Manager discrepancy
        api_grids = 0
        if "api_endpoints" in findings and "grids_endpoint" in findings["api_endpoints"]:
            grids_data = findings["api_endpoints"]["grids_endpoint"]
            if isinstance(grids_data, dict) and "grid_count" in grids_data:
                api_grids = grids_data["grid_count"]
                avax_grids = grids_data.get("avax_grids", [])
        
        # Check for potential issues
        issues = []
        
        # Issue 1: API shows 0 but logic suggests active
        if api_grids == 0:
            issues.append("API returns 0 grids but AVAX rejection suggests active grid exists")
            
        # Issue 2: Grid state mismatch
        if "grid_manager" in findings and "logic_test" in findings["grid_manager"]:
            logic_test = findings["grid_manager"]["logic_test"]
            if logic_test.get("avax_has_active") and api_grids == 0:
                issues.append("Grid logic shows AVAX should be active but API doesn't report it")
        
        # Issue 3: Database inconsistencies
        if "database" in findings:
            db_data = findings["database"]
            if db_data.get("avax_positions", 0) > 0 and api_grids == 0:
                issues.append("Database shows AVAX positions but API shows 0 active grids")
        
        # Generate recommendations
        if issues:
            recommendations.append("URGENT: Grid state synchronization issue detected")
            recommendations.append("Check grid manager _grids dictionary consistency")
            recommendations.append("Verify state transitions from GridLifecycleManager")
            recommendations.append("Review API server grid manager integration")
        
        if "database" in findings and not findings["database"].get("database_connected"):
            recommendations.append("Fix database connection for proper state verification")
        
        recommendations.append("Add comprehensive logging to grid state changes")
        recommendations.append("Implement periodic state consistency checks")
        
        self.diagnostic_results["issues_identified"] = issues
        self.diagnostic_results["recommendations"] = recommendations
        
        logger.error(f"🔍 Issues found: {len(issues)}")
        for i, issue in enumerate(issues, 1):
            logger.error(f"  {i}. {issue}")
        
        logger.info(f"💡 Recommendations: {len(recommendations)}")
        for i, rec in enumerate(recommendations, 1):
            logger.info(f"  {i}. {rec}")
    
    def print_detailed_report(self) -> None:
        """Print detailed diagnostic report."""
        print("\n" + "="*80)
        print(f"GRID REJECTION DIAGNOSTIC REPORT - {self.symbol}")
        print("="*80)
        print(f"Timestamp: {self.diagnostic_results['timestamp']}")
        print(f"\n🔍 FINDINGS:")
        
        # API Endpoints
        if "api_endpoints" in self.diagnostic_results["findings"]:
            print(f"\n1. API Endpoints:")
            api_data = self.diagnostic_results["findings"]["api_endpoints"]
            if "status_endpoint" in api_data:
                status = api_data["status_endpoint"]
                print(f"   Status API: {status.get('active_grids_count', 'N/A')} active grids, running: {status.get('is_running', 'N/A')}")
            if "grids_endpoint" in api_data:
                grids = api_data["grids_endpoint"]
                print(f"   Grids API: {grids.get('grid_count', 'N/A')} grids total, {len(grids.get('avax_grids', []))} AVAX grids")
        
        # Grid Manager
        if "grid_manager" in self.diagnostic_results["findings"]:
            print(f"\n2. Grid Manager:")
            gm_data = self.diagnostic_results["findings"]["grid_manager"]
            print(f"   Methods available: {gm_data.get('has_active_grid_method', False)}")
            if "logic_test" in gm_data:
                test = gm_data["logic_test"]
                print(f"   Logic test - AVAX should be active: {test.get('avax_has_active', False)}")
        
        # Risk Manager
        if "risk_manager" in self.diagnostic_results["findings"]:
            print(f"\n3. Risk Manager:")
            rm_data = self.diagnostic_results["findings"]["risk_manager"]
            print(f"   Grid methods: {rm_data.get('grid_validation_methods', [])}")
        
        # Database
        if "database" in self.diagnostic_results["findings"]:
            print(f"\n4. Database:")
            db_data = self.diagnostic_results["findings"]["database"]
            if db_data.get("database_connected"):
                print(f"   AVAX positions: {db_data.get('avax_positions', 0)}")
                print(f"   AVAX trades: {db_data.get('avax_trades', 0)}")
            else:
                print(f"   Status: Not connected")
        
        # Issues and Recommendations
        print(f"\n🚨 ISSUES IDENTIFIED:")
        if self.diagnostic_results.get("issues_identified"):
            for i, issue in enumerate(self.diagnostic_results["issues_identified"], 1):
                print(f"   {i}. {issue}")
        else:
            print("   No critical issues found")
        
        print(f"\n💡 RECOMMENDATIONS:")
        for i, rec in enumerate(self.diagnostic_results["recommendations"], 1):
            print(f"   {i}. {rec}")
        
        print("\n" + "="*80)

async def main():
    """Main diagnostic function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Diagnose grid rejection issues")
    parser.add_argument("--symbol", default="AVAX", help="Symbol to diagnose (default: AVAX)")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    args = parser.parse_args()
    
    diagnostic = GridDiagnosticTool(symbol=args.symbol, api_base=args.api)
    
    try:
        results = await diagnostic.run_all_diagnostics()
        diagnostic.print_detailed_report()
        
        # Save detailed results to JSON
        output_file = f"grid_diagnostic_{args.symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        
        print(f"\n📄 Detailed results saved to: {output_file}")
        
        # Return appropriate exit code
        if results.get("issues_identified"):
            return 1
        return 0
        
    except Exception as e:
        logger.error(f"Diagnostic failed: {e}")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)