#!/usr/bin/env python3
"""
Server Stability Monitoring Script

Monitors API server stability, database connections, and request handling.
Performs automated stability testing and load testing capabilities.
"""

import requests
import time
import threading
import statistics
from datetime import datetime, timedelta
import json
import sys
import os
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DATABASE_PATH
import sqlite3


class ServerStabilityMonitor:
    """Monitor API server stability and performance."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.timeout = 10  # 10 second timeout for all requests

    def test_health_endpoint(self) -> Dict[str, Any]:
        """Test the health endpoint."""
        try:
            start_time = time.time()
            response = self.session.get(f"{self.base_url}/api/health")
            response_time = time.time() - start_time

            return {
                "endpoint": "/api/health",
                "status_code": response.status_code,
                "response_time": response_time,
                "success": response.status_code == 200,
                "error": None if response.status_code == 200 else response.text
            }
        except Exception as e:
            return {
                "endpoint": "/api/health",
                "status_code": None,
                "response_time": None,
                "success": False,
                "error": str(e)
            }

    def test_database_connection(self) -> Dict[str, Any]:
        """Test database connectivity."""
        try:
            start_time = time.time()
            conn = sqlite3.connect(DATABASE_PATH, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
            table_count = cursor.fetchone()[0]
            conn.close()
            connection_time = time.time() - start_time

            return {
                "test": "database_connection",
                "success": True,
                "connection_time": connection_time,
                "table_count": table_count,
                "error": None
            }
        except Exception as e:
            return {
                "test": "database_connection",
                "success": False,
                "connection_time": None,
                "table_count": None,
                "error": str(e)
            }

    def test_concurrent_requests(self, num_requests: int = 10, num_threads: int = 5) -> Dict[str, Any]:
        """Test concurrent request handling."""
        results = []
        errors = []

        def make_request():
            try:
                start_time = time.time()
                response = self.session.get(f"{self.base_url}/api/health")
                response_time = time.time() - start_time

                results.append({
                    "status_code": response.status_code,
                    "response_time": response_time,
                    "success": response.status_code == 200
                })
            except Exception as e:
                errors.append(str(e))
                results.append({
                    "status_code": None,
                    "response_time": None,
                    "success": False
                })

        start_time = time.time()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(make_request) for _ in range(num_requests)]
            for future in as_completed(futures):
                future.result()  # Wait for completion

        total_time = time.time() - start_time

        successful_requests = sum(1 for r in results if r["success"])
        response_times = [r["response_time"] for r in results if r["response_time"] is not None]

        return {
            "test": "concurrent_requests",
            "total_requests": num_requests,
            "successful_requests": successful_requests,
            "success_rate": successful_requests / num_requests * 100,
            "total_time": total_time,
            "avg_response_time": statistics.mean(response_times) if response_times else None,
            "min_response_time": min(response_times) if response_times else None,
            "max_response_time": max(response_times) if response_times else None,
            "errors": errors
        }

    def test_api_endpoints(self) -> Dict[str, Any]:
        """Test key API endpoints."""
        endpoints = [
            "/api/health",
            "/api/status",
            "/api/server/status",
            "/api/market-data",
            "/api/positions"
        ]

        results = {}

        for endpoint in endpoints:
            try:
                start_time = time.time()
                response = self.session.get(f"{self.base_url}{endpoint}", timeout=15)
                response_time = time.time() - start_time

                results[endpoint] = {
                    "status_code": response.status_code,
                    "response_time": response_time,
                    "success": response.status_code == 200,
                    "error": None
                }
            except Exception as e:
                results[endpoint] = {
                    "status_code": None,
                    "response_time": None,
                    "success": False,
                    "error": str(e)
                }

        return {
            "test": "api_endpoints",
            "results": results,
            "overall_success": all(r["success"] for r in results.values())
        }

    def run_stability_test(self, duration_minutes: int = 5) -> Dict[str, Any]:
        """Run extended stability test."""
        print(f"Running stability test for {duration_minutes} minutes...")

        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=duration_minutes)

        health_checks = []
        errors = []

        check_interval = 10  # Check every 10 seconds

        while datetime.now() < end_time:
            try:
                result = self.test_health_endpoint()
                health_checks.append(result)

                if not result["success"]:
                    errors.append(f"Health check failed: {result['error']}")

                # Also test a few key endpoints occasionally
                if len(health_checks) % 6 == 0:  # Every minute
                    endpoint_test = self.test_api_endpoints()
                    if not endpoint_test["overall_success"]:
                        errors.append("API endpoint test failed")

            except Exception as e:
                errors.append(f"Stability test error: {str(e)}")

            time.sleep(check_interval)

        successful_checks = sum(1 for check in health_checks if check["success"])
        total_checks = len(health_checks)

        response_times = [check["response_time"] for check in health_checks if check["response_time"] is not None]

        return {
            "test": "stability_test",
            "duration_minutes": duration_minutes,
            "total_checks": total_checks,
            "successful_checks": successful_checks,
            "success_rate": successful_checks / total_checks * 100 if total_checks > 0 else 0,
            "avg_response_time": statistics.mean(response_times) if response_times else None,
            "errors": errors,
            "start_time": start_time.isoformat(),
            "end_time": datetime.now().isoformat()
        }

    def generate_report(self, test_results: List[Dict[str, Any]]) -> str:
        """Generate a stability report."""
        report = []
        report.append("=" * 60)
        report.append("SERVER STABILITY MONITOR REPORT")
        report.append("=" * 60)
        report.append(f"Generated: {datetime.now().isoformat()}")
        report.append("")

        for result in test_results:
            report.append(f"Test: {result.get('test', 'Unknown')}")
            report.append("-" * 40)

            if result["test"] == "health_check":
                report.append(f"Status: {'PASS' if result['success'] else 'FAIL'}")
                report.append(f"Response Time: {result['response_time']:.3f}s")
                if result.get("error"):
                    report.append(f"Error: {result['error']}")

            elif result["test"] == "database_connection":
                report.append(f"Status: {'PASS' if result['success'] else 'FAIL'}")
                if result["success"]:
                    report.append(f"Connection Time: {result['connection_time']:.3f}s")
                    report.append(f"Tables Found: {result['table_count']}")
                else:
                    report.append(f"Error: {result['error']}")

            elif result["test"] == "concurrent_requests":
                report.append(f"Total Requests: {result['total_requests']}")
                report.append(f"Successful: {result['successful_requests']}")
                report.append(f"Success Rate: {result['success_rate']:.1f}%")
                report.append(f"Total Time: {result['total_time']:.3f}s")
                if result.get("avg_response_time"):
                    report.append(f"Avg Response Time: {result['avg_response_time']:.3f}s")
                if result["errors"]:
                    report.append(f"Errors: {len(result['errors'])}")

            elif result["test"] == "api_endpoints":
                report.append(f"Overall Status: {'PASS' if result['overall_success'] else 'FAIL'}")
                for endpoint, endpoint_result in result["results"].items():
                    status = "PASS" if endpoint_result["success"] else "FAIL"
                    report.append(f"  {endpoint}: {status}")
                    if endpoint_result.get("response_time"):
                        report.append(f"    Response Time: {endpoint_result['response_time']:.3f}s")

            elif result["test"] == "stability_test":
                report.append(f"Duration: {result['duration_minutes']} minutes")
                report.append(f"Total Checks: {result['total_checks']}")
                report.append(f"Successful: {result['successful_checks']}")
                report.append(f"Success Rate: {result['success_rate']:.1f}%")
                if result.get("avg_response_time"):
                    report.append(f"Avg Response Time: {result['avg_response_time']:.3f}s")
                if result["errors"]:
                    report.append(f"Errors: {len(result['errors'])}")
                    for error in result["errors"][:5]:  # Show first 5 errors
                        report.append(f"  - {error}")

            report.append("")

        return "\n".join(report)


def main():
    """Main monitoring function."""
    monitor = ServerStabilityMonitor()

    print("Server Stability Monitor")
    print("=" * 40)

    # Run basic health check
    print("1. Testing health endpoint...")
    health_result = monitor.test_health_endpoint()
    print(f"   Status: {'PASS' if health_result['success'] else 'FAIL'}")

    # Test database connection
    print("2. Testing database connection...")
    db_result = monitor.test_database_connection()
    print(f"   Status: {'PASS' if db_result['success'] else 'FAIL'}")

    # Test concurrent requests
    print("3. Testing concurrent requests...")
    concurrent_result = monitor.test_concurrent_requests(num_requests=20, num_threads=5)
    print(f"   Success Rate: {concurrent_result['success_rate']:.1f}%")

    # Test API endpoints
    print("4. Testing API endpoints...")
    api_result = monitor.test_api_endpoints()
    print(f"   Overall Status: {'PASS' if api_result['overall_success'] else 'FAIL'}")

    # Run extended stability test
    print("5. Running extended stability test (2 minutes)...")
    stability_result = monitor.run_stability_test(duration_minutes=2)
    print(f"   Success Rate: {stability_result['success_rate']:.1f}%")

    # Generate and print report
    test_results = [
        {"test": "health_check", **health_result},
        db_result,
        concurrent_result,
        api_result,
        stability_result
    ]

    report = monitor.generate_report(test_results)
    print("\n" + report)

    # Save report to file
    report_file = f"stability_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_file, 'w') as f:
        f.write(report)

    print(f"\nReport saved to: {report_file}")

    # Exit with appropriate code
    all_passed = all([
        health_result["success"],
        db_result["success"],
        concurrent_result["success_rate"] >= 95,
        api_result["overall_success"],
        stability_result["success_rate"] >= 95
    ])

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()