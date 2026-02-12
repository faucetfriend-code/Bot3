#!/usr/bin/env python3
"""
Simple AVAX Grid Diagnostic Tool - No Unicode
"""

import asyncio
import json
import sys
import requests
from datetime import datetime
from pathlib import Path

class SimpleAVAXDiagnostic:
    """Simple diagnostic for AVAX grid state issues."""
    
    def __init__(self, api_base: str = "http://localhost:8000"):
        self.api_base = api_base
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "checks": {},
            "issues": [],
            "recommendations": []
        }
    
    async def run_diagnostic(self) -> dict:
        """Run complete diagnostic suite."""
        print("Starting AVAX Grid Diagnostic...")
        print("=" * 60)
        
        # Check API availability
        await self.check_api_availability()
        
        # Check grid endpoints
        await self.check_grid_endpoints()
        
        # Analyze consistency
        await self.analyze_consistency()
        
        return self.results
    
    async def check_api_availability(self) -> None:
        """Check if API server is running."""
        print("\n1. Checking API Availability...")
        
        try:
            response = requests.get(f"{self.api_base}/api/status", timeout=5)
            if response.status_code == 200:
                status = response.json()
                self.results["checks"]["api_available"] = True
                self.results["checks"]["bot_running"] = status.get("is_running", False)
                active_grids = status.get("active_grids", "N/A")
                print(f"   OK - API Server Online")
                print(f"   Bot Running: {status.get('is_running', False)}")
                print(f"   Active Grids: {active_grids}")
            else:
                self.results["checks"]["api_available"] = False
                print(f"   ERROR - API Server: {response.status_code}")
                
        except requests.exceptions.ConnectionError:
            self.results["checks"]["api_available"] = False
            print("   ERROR - API Server: Connection refused")
            print("   Make sure trading bot is running on port 8000")
        except Exception as e:
            self.results["checks"]["api_available"] = False
            print(f"   ERROR - API Server: {e}")
    
    async def check_grid_endpoints(self) -> None:
        """Check grid-specific endpoints."""
        print("\n2. Checking Grid Endpoints...")
        
        try:
            response = requests.get(f"{self.api_base}/api/grids", timeout=5)
            if response.status_code == 200:
                grids_data = response.json()
                if isinstance(grids_data, list):
                    avax_grids = [g for g in grids_data if isinstance(g, dict) and g.get("symbol") == "AVAX"]
                    self.results["checks"]["grids_endpoint"] = {
                        "total_grids": len(grids_data),
                        "avax_grids": len(avax_grids),
                        "grid_details": avax_grids[:2]
                    }
                    print(f"   OK - Grids API: {len(grids_data)} total grids")
                    print(f"   AVAX Grids: {len(avax_grids)}")
                    
                    # Show AVAX grid details
                    if avax_grids:
                        for i, grid in enumerate(avax_grids[:2], 1):
                            state = grid.get("state", "unknown")
                            levels = len(grid.get("grid_levels", []))
                            print(f"     Grid {i}: State={state}, Levels={levels}")
                else:
                    print(f"   WARNING - Grids API: Unexpected format")
                    self.results["checks"]["grids_endpoint"] = {"error": "unexpected_format"}
            else:
                print(f"   ERROR - Grids API: {response.status_code}")
                self.results["checks"]["grids_endpoint"] = {"error": response.status_code}
                
        except Exception as e:
            print(f"   ERROR - Grids API: {e}")
            self.results["checks"]["grids_endpoint"] = {"error": str(e)}
    
    async def analyze_consistency(self) -> None:
        """Analyze consistency between data sources."""
        print("\n3. Analyzing Data Consistency...")
        
        issues = []
        
        if not self.results.get("checks", {}).get("api_available", False):
            issues.append("API server not available")
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
            if isinstance(grids_data, dict):
                grids_api_grids = grids_data.get("total_grids", 0)
                avax_grids_count = grids_data.get("avax_grids", 0)
        
        print(f"   Status API reports: {status_grids} active grids")
        print(f"   Grids API reports: {grids_api_grids} total grids")
        print(f"   AVAX grids found: {avax_grids_count}")
        
        # Consistency checks
        if status_grids != grids_api_grids:
            issues.append(f"Grid count mismatch: Status={status_grids}, Grids={grids_api_grids}")
            print(f"   WARNING - Grid count mismatch!")
        
        if avax_grids_count == 0 and status_grids > 0:
            issues.append("AVAX shows 0 grids but total > 0")
            print(f"   WARNING - AVAX grid discrepancy!")
        
        if status_grids == 0 and avax_grids_count > 0:
            issues.append("Status shows 0 grids but AVAX found")
            print(f"   WARNING - API inconsistency!")
        
        if not issues:
            print("   OK - Data consistency appears normal")
        
        self.results["checks"]["consistency_analysis"] = {
            "status_grids": status_grids,
            "grids_api_grids": grids_api_grids,
            "avax_grids_count": avax_grids_count,
            "consistent": len(issues) == 0
        }
        
        self.results["issues"].extend(issues)
    
    def print_summary(self) -> None:
        """Print diagnostic summary."""
        print("\n" + "=" * 60)
        print("AVAX GRID DIAGNOSTIC SUMMARY")
        print("=" * 60)
        print(f"Timestamp: {self.results['timestamp']}")
        
        api_available = self.results.get("checks", {}).get("api_available", False)
        print(f"API Server: {'Online' if api_available else 'Offline'}")
        
        if api_available:
            bot_running = self.results.get("checks", {}).get("bot_running", "Unknown")
            print(f"Bot Running: {bot_running}")
            
            if "consistency_analysis" in self.results["checks"]:
                analysis = self.results["checks"]["consistency_analysis"]
                print(f"Grid Counts - Status: {analysis.get('status_grids', 0)}, API: {analysis.get('grids_api_grids', 0)}, AVAX: {analysis.get('avax_grids_count', 0)}")
        
        print(f"\nIssues Found: {len(self.results.get('issues', []))}")
        for issue in self.results.get('issues', []):
            print(f"  - {issue}")
        
        print(f"\nRecommendations:")
        for rec in self.results.get('recommendations', [])[:3]:
            print(f"  - {rec}")
        
        print("\n" + "=" * 60)

async def main():
    """Main diagnostic execution."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Diagnose AVAX grid rejection issues")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    args = parser.parse_args()
    
    diagnostic = SimpleAVAXDiagnostic(api_base=args.api)
    
    try:
        results = await diagnostic.run_diagnostic()
        diagnostic.print_summary()
        
        # Generate recommendations based on findings
        recommendations = []
        
        if not results.get("checks", {}).get("api_available", False):
            recommendations.append("Start the trading bot API server")
            recommendations.append("Verify bot is running on port 8000")
        else:
            issues = results.get("issues", [])
            if issues:
                if "mismatch" in " ".join(issues).lower():
                    recommendations.append("Check GridLifecycleManager._grids dictionary consistency")
                    recommendations.append("Verify get_all_active_grids() method implementation")
                
                if any("AVAX" in issue for issue in issues):
                    recommendations.append("Investigate AVAX-specific grid state tracking")
                    recommendations.append("Check if AVAX grid exists but not in ACTIVE state")
                
                if any("inconsistency" in issue for issue in issues):
                    recommendations.append("Review API endpoint data synchronization")
                    recommendations.append("Check for race conditions in grid state updates")
            else:
                recommendations.append("System appears consistent - check other causes for AVAX rejection")
                recommendations.append("Review risk manager validation logic")
        
        # Add general recommendations
        recommendations.extend([
            "Add comprehensive logging for grid state changes",
            "Implement periodic grid state consistency checks"
        ])
        
        results["recommendations"] = recommendations
        
        # Save results
        output_file = f"avax_diagnostic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        
        print(f"\nDetailed results saved to: {output_file}")
        
        return 1 if results.get("issues") else 0
        
    except KeyboardInterrupt:
        print("\nDiagnostic interrupted by user")
        return 130
    except Exception as e:
        print(f"\nDiagnostic failed: {e}")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)