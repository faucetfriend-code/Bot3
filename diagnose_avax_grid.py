#!/usr/bin/env python3
"""
Focused diagnostic tool for AVAX grid rejection vs display disconnect.

This script focuses on API consistency and data state without importing problematic modules.
Usage:
    python diagnose_avax_grid.py [--api http://localhost:8000]
"""

import asyncio
import json
import sys
import time
import requests
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

class AVAXGridDiagnostic:
    """Focused diagnostic for AVAX grid state issues."""
    
    def __init__(self, api_base: str = "http://localhost:8000"):
        self.api_base = api_base
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "checks": {},
            "issues": [],
            "recommendations": []
        }
    
    async def run_diagnostic(self) -> Dict[str, Any]:
        """Run complete diagnostic suite."""
        print("Starting AVAX Grid Rejection Diagnostic...")
        print("=" * 60)
        
        # Check API availability
        await self._check_api_availability()
        
        # Check grid-related endpoints
        await self._check_grid_endpoints()
        
        # Check trading bot status
        await self._check_trading_status()
        
        # Analyze consistency
        await self._analyze_consistency()
        
        # Generate findings
        self._generate_findings()
        
        return self.results
    
    async def _check_api_availability(self) -> None:
        """Check if API server is running."""
        print("\n1. Checking API Availability...")
        
        try:
            response = requests.get(f"{self.api_base}/api/status", timeout=5)
            if response.status_code == 200:
                status = response.json()
                self.results["checks"]["api_available"] = True
                self.results["checks"]["bot_running"] = status.get("is_running", False)
                print(f"   ✅ API Server: Online")
                print(f"   ✅ Bot Running: {status.get('is_running', False)}")
                print(f"   📊 Active Grids (Status): {status.get('active_grids', 'N/A')}")
            else:
                self.results["checks"]["api_available"] = False
                print(f"   ❌ API Server: Error {response.status_code}")
                
        except requests.exceptions.ConnectionError:
            self.results["checks"]["api_available"] = False
            print("   ❌ API Server: Connection refused")
            print("   💡 Make sure the trading bot is running on port 8000")
        except Exception as e:
            self.results["checks"]["api_available"] = False
            print(f"   ❌ API Server: {e}")
    
    async def _check_grid_endpoints(self) -> None:
        """Check grid-specific endpoints."""
        print("\n📊 2. Checking Grid Endpoints...")
        
        # Check /api/grids
        try:
            response = requests.get(f"{self.api_base}/api/grids", timeout=5)
            if response.status_code == 200:
                grids_data = response.json()
                if isinstance(grids_data, list):
                    avax_grids = [g for g in grids_data if isinstance(g, dict) and g.get("symbol") == "AVAX"]
                    self.results["checks"]["grids_endpoint"] = {
                        "total_grids": len(grids_data),
                        "avax_grids": len(avax_grids),
                        "grid_details": avax_grids[:3]  # First 3 AVAX grids
                    }
                    print(f"   ✅ Grids API: {len(grids_data)} total grids")
                    print(f"   🔍 AVAX Grids: {len(avax_grids)}")
                    
                    # Show AVAX grid details if found
                    if avax_grids:
                        for i, grid in enumerate(avax_grids[:2], 1):
                            state = grid.get("state", "unknown")
                            print(f"      Grid {i}: State={state}, Levels={len(grid.get('grid_levels', []))}")
                else:
                    print(f"   ⚠️  Grids API: Unexpected format ({type(grids_data)})")
                    self.results["checks"]["grids_endpoint"] = {"error": "unexpected_format"}
            else:
                print(f"   ❌ Grids API: Error {response.status_code}")
                self.results["checks"]["grids_endpoint"] = {"error": response.status_code}
                
        except Exception as e:
            print(f"   ❌ Grids API: {e}")
            self.results["checks"]["grids_endpoint"] = {"error": str(e)}
    
    async def _check_trading_status(self) -> None:
        """Check detailed trading status."""
        print("\n⚙️  3. Checking Trading Status...")
        
        try:
            response = requests.get(f"{self.api_base}/api/trading-status", timeout=5)
            if response.status_code == 200:
                trading_data = response.json()
                self.results["checks"]["trading_status"] = trading_data
                
                # Extract relevant info
                is_running = trading_data.get("is_running", False)
                has_grid_lifecycle = trading_data.get("has_grid_lifecycle", False)
                
                print(f"   ✅ Trading Status: {trading_data.get('status', 'unknown')}")
                print(f"   🤖 Bot Running: {is_running}")
                print(f"   📋 Grid Lifecycle: {has_grid_lifecycle}")
                
                # Look for AVAX-specific info
                if "signals" in trading_data:
                    signals = trading_data["signals"]
                    if isinstance(signals, dict):
                        avax_signals = [s for s in signals.keys() if "AVAX" in str(s)]
                        if avax_signals:
                            print(f"   📡 AVAX Signals: {len(avax_signals)}")
                            for signal in avax_signals[:3]:
                                print(f"      - {signal}")
                        
            else:
                print(f"   ❌ Trading Status: Error {response.status_code}")
                self.results["checks"]["trading_status"] = {"error": response.status_code}
                
        except Exception as e:
            print(f"   ❌ Trading Status: {e}")
            self.results["checks"]["trading_status"] = {"error": str(e)}
    
    async def _analyze_consistency(self) -> None:
        """Analyze consistency between different data sources."""
        print("\n🔍 4. Analyzing Data Consistency...")
        
        issues = []
        
        # Check API availability first
        if not self.results.get("checks", {}).get("api_available", False):
            issues.append("API server not available for consistency check")
            self.results["issues"] = issues
            return
        
        # Extract data points
        status_grids = 0
        grids_api_grids = 0
        avax_grids_count = 0
        
        # From /api/status
        try:
            response = requests.get(f"{self.api_base}/api/status", timeout=5)
            if response.status_code == 200:
                status_data = response.json()
                status_grids = status_data.get("active_grids", 0)
        except:
            pass
        
        # From /api/grids  
        if "grids_endpoint" in self.results["checks"]:
            grids_data = self.results["checks"]["grids_endpoint"]
            if isinstance(grids_data, dict) and "total_grids" in grids_data:
                grids_api_grids = grids_data["total_grids"]
                avax_grids_count = grids_data.get("avax_grids", 0)
        
        print(f"   📊 Status API reports: {status_grids} active grids")
        print(f"   📊 Grids API reports: {grids_api_grids} total grids")
        print(f"   📊 AVAX grids found: {avax_grids_count}")
        
        # Consistency checks
        if status_grids != grids_api_grids:
            issues.append(f"Grid count mismatch: Status={status_grids}, Grids={grids_api_grids}")
            print(f"   ⚠️  Grid count mismatch detected!")
        
        if avax_grids_count == 0 and status_grids > 0:
            issues.append("AVAX shows 0 grids but total > 0 - possible state tracking issue")
            print(f"   ⚠️  AVAX grid discrepancy detected!")
        
        if status_grids == 0 and avax_grids_count > 0:
            issues.append("Status shows 0 grids but AVAX found - API inconsistency")
            print(f"   ⚠️  API inconsistency detected!")
        
        if not issues:
            print("   ✅ Data consistency appears normal")
        
        self.results["checks"]["consistency_analysis"] = {
            "status_grids": status_grids,
            "grids_api_grids": grids_api_grids,
            "avax_grids_count": avax_grids_count,
            "consistent": len(issues) == 0
        }
        
        self.results["issues"].extend(issues)
    
    def _generate_findings(self) -> None:
        """Generate final findings and recommendations."""
        print("\n📝 5. Generating Findings and Recommendations...")
        
        issues = self.results.get("issues", [])
        recommendations = []
        
        if not self.results.get("checks", {}).get("api_available", False):
            recommendations.append("Start the trading bot API server")
            recommendations.append("Verify bot is running on port 8000")
        else:
            if issues:
                # Grid state synchronization issues
                if "mismatch" in " ".join(issues).lower():
                    recommendations.append("Check GridLifecycleManager._grids dictionary consistency")
                    recommendations.append("Verify get_all_active_grids() method implementation")
                    recommendations.append("Review API server grid manager integration")
                
                # AVAX-specific issues
                if any("AVAX" in issue for issue in issues):
                    recommendations.append("Investigate AVAX-specific grid state tracking")
                    recommendations.append("Check if AVAX grid exists but not in ACTIVE state")
                    recommendations.append("Verify grid state transitions for AVAX")
                
                # API consistency
                if any("inconsistency" in issue for issue in issues):
                    recommendations.append("Review API endpoint data synchronization")
                    recommendations.append("Check for race conditions in grid state updates")
                    recommendations.append("Implement grid state validation checks")
            else:
                recommendations.append("System appears consistent - investigate other causes for AVAX rejection")
                recommendations.append("Check trading logs for AVAX-specific rejection messages")
                recommendations.append("Review risk manager validation logic")
        
        # Add general recommendations
        recommendations.extend([
            "Add comprehensive logging for grid state changes",
            "Implement periodic grid state consistency checks",
            "Add real-time grid status monitoring"
        ])
        
        self.results["recommendations"] = recommendations
        
        print(f"   📋 Issues Found: {len(issues)}")
        for i, issue in enumerate(issues, 1):
            print(f"      {i}. {issue}")
        
        print(f"\n   💡 Recommendations: {len(recommendations)}")
        for i, rec in enumerate(recommendations[:5], 1):
            print(f"      {i}. {rec}")
        
        if len(recommendations) > 5:
            print(f"      ... and {len(recommendations) - 5} more")
    
    def print_summary(self) -> None:
        """Print diagnostic summary."""
        print("\n" + "=" * 60)
        print("AVAX GRID REJECTION DIAGNOSTIC SUMMARY")
        print("=" * 60)
        print(f"Timestamp: {self.results['timestamp']}")
        print(f"API Server: {'✅ Online' if self.results.get('checks', {}).get('api_available') else '❌ Offline'}")
        
        if self.results.get("checks", {}).get("api_available"):
            print(f"Bot Running: {self.results.get('checks', {}).get('bot_running', 'Unknown')}")
            
            if "consistency_analysis" in self.results["checks"]:
                analysis = self.results["checks"]["consistency_analysis"]
                print(f"Grid Counts - Status: {analysis.get('status_grids', 0)}, API: {analysis.get('grids_api_grids', 0)}, AVAX: {analysis.get('avax_grids_count', 0)}")
        
        print(f"\n🚨 Critical Issues: {len(self.results.get('issues', []))}")
        for issue in self.results.get('issues', []):
            print(f"  • {issue}")
        
        print(f"\n💡 Top Recommendations:")
        for rec in self.results.get('recommendations', [])[:3]:
            print(f"  • {rec}")
        
        print("\n" + "=" * 60)

async def main():
    """Main diagnostic execution."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Diagnose AVAX grid rejection issues")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    args = parser.parse_args()
    
    diagnostic = AVAXGridDiagnostic(api_base=args.api)
    
    try:
        results = await diagnostic.run_diagnostic()
        diagnostic.print_summary()
        
        # Save results
        output_file = f"avax_grid_diagnostic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        
        print(f"\n📄 Detailed results saved to: {output_file}")
        
        # Return exit code based on findings
        return 1 if results.get("issues") else 0
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Diagnostic interrupted by user")
        return 130
    except Exception as e:
        print(f"\n\n❌ Diagnostic failed: {e}")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)