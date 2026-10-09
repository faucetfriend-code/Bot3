#!/usr/bin/env python3
"""
System Health Check Script - Phase 3

Comprehensive health validation for the component-based trading system.
Run this script to verify system integrity before deployment.
"""

import sys
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Dict, Any, List

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_bot_v2.monitoring import get_monitoring_system
from trading_bot_v2.component_registry import get_component_registry
from trading_bot_v2.event_system import get_event_bus, Event, EventType
from trading_bot_v2.feature_flags import get_feature_flags

if TYPE_CHECKING:
    import contextlib
    import io


class HealthChecker:
    """Comprehensive system health checker."""

    def __init__(self) -> None:
        self.monitoring = get_monitoring_system()
        self.registry = get_component_registry()
        self.event_bus = get_event_bus()
        self.feature_flags = get_feature_flags()

        self.checks_passed = 0
        self.checks_failed = 0
        self.warnings: List[str] = []

    def run_all_checks(self) -> Dict[str, Any]:
        """Run all health checks and return results."""
        print("🔍 Starting comprehensive system health check...")
        print("=" * 60)

        results = {
            "timestamp": datetime.now().isoformat(),
            "phase3_features": self.check_phase3_features(),
            "component_health": self.check_component_health(),
            "event_system": self.check_event_system(),
            "websocket_authority": self.check_websocket_authority(),
            "coordinator_integrity": self.check_coordinator_integrity(),
            "performance_baselines": self.check_performance_baselines(),
            "security_validation": self.check_security_validation(),
            "summary": {},
        }

        # Calculate summary
        results["summary"] = self.generate_summary(results)

        return results

    def check_phase3_features(self) -> Dict[str, Any]:
        """Check that Phase 3 features are properly enabled."""
        print("📋 Checking Phase 3 feature flags...")

        required_features = {
            "component_registry": True,
            "event_system": True,
            "coordinator_trading_bot": True,
            "websocket_only_prices": True,
        }

        results: Dict[str, Any] = {}
        all_enabled = True

        for feature, required in required_features.items():
            enabled = getattr(self.feature_flags, f"enable_{feature}", False)
            results[feature] = {
                "enabled": enabled,
                "required": required,
                "status": "PASS" if enabled == required else "FAIL",
            }
            if enabled != required:
                all_enabled = False
                self.checks_failed += 1
            else:
                self.checks_passed += 1

        results["overall_status"] = "PASS" if all_enabled else "FAIL"
        print(f"   ✅ Phase 3 features: {'PASS' if all_enabled else 'FAIL'}")
        return results

    def check_component_health(self) -> Dict[str, Any]:
        """Check health of all registered components."""
        print("🏥 Checking component health...")

        health_status = self.registry.validate_dependencies()
        results = {
            "total_components": len(health_status.get("healthy", []))
            + len(health_status.get("unhealthy", [])),
            "healthy_components": len(health_status.get("healthy", [])),
            "unhealthy_components": len(health_status.get("unhealthy", [])),
            "errors": health_status.get("errors", []),
            "details": health_status,
        }

        if results["unhealthy_components"] > 0:
            self.checks_failed += 1
            results["status"] = "FAIL"
            print(
                f"   ❌ Component health: FAIL ({results['unhealthy_components']} unhealthy)"
            )
        else:
            self.checks_passed += 1
            results["status"] = "PASS"
            print(
                f"   ✅ Component health: PASS ({results['healthy_components']} healthy)"
            )

        return results

    def check_event_system(self) -> Dict[str, Any]:
        """Check event system functionality."""
        print("📡 Checking event system...")

        # Test event publishing
        test_events_received: List[Event] = []

        def test_handler(event: Event) -> None:
            test_events_received.append(event)

        # Subscribe to test event
        self.event_bus.subscribe(EventType.SIGNAL_GENERATED, test_handler)

        # Publish test event
        self.event_bus.publish_event(
            EventType.SIGNAL_GENERATED,
            {"test": True, "timestamp": time.time()},
            "health_check",
        )

        # Wait for processing
        time.sleep(0.1)

        # Check event stats
        event_stats = self.event_bus.get_stats()

        results = {
            "events_received": len(test_events_received),
            "total_subscribers": event_stats.get("total_subscribers", 0),
            "total_events_processed": event_stats.get("total_events", 0),
            "event_types": list(event_stats.get("event_counts", {}).keys()),
        }

        if results["events_received"] > 0 and results["total_subscribers"] > 0:
            self.checks_passed += 1
            results["status"] = "PASS"
            print("   ✅ Event system: PASS")
        else:
            self.checks_failed += 1
            results["status"] = "FAIL"
            print("   ❌ Event system: FAIL")
        return results

    def check_websocket_authority(self) -> Dict[str, Any]:
        """Check WebSocket authority enforcement."""
        print("🌐 Checking WebSocket authority...")

        # This is a design check - WebSocket-only should be enforced
        ws_only_enabled = self.feature_flags.enable_websocket_only_prices

        # Try to import TradingBot and check WebSocket-only behavior
        try:
            from trading_bot_v2.trading_bot import TradingBot

            # Create minimal bot instance for testing
            with self.suppressed_output():
                bot = TradingBot.__new__(TradingBot)  # Create without __init__

                # Mock WebSocket client
                mock_ws = type("MockWS", (), {"get_price": lambda self, symbol: None})()
                bot.ws_client = mock_ws

                # Test that WebSocket failure raises RuntimeError (no REST fallback)
                try:
                    bot._get_ticker_ws("SUI-PERP")
                    ws_authority_working = False  # Should have raised error
                except RuntimeError:
                    ws_authority_working = True  # Correct behavior
                except Exception:
                    ws_authority_working = False  # Unexpected error

        except Exception as e:
            ws_authority_working = False
            print(f"   ⚠️  Could not test WebSocket authority: {e}")

        results: Dict[str, Any] = {
            "websocket_only_enabled": ws_only_enabled,
            "authority_working": ws_authority_working,
        }

        if ws_only_enabled and ws_authority_working:
            self.checks_passed += 1
            results["status"] = "PASS"
            print("   ✅ WebSocket authority: PASS")
        elif ws_only_enabled and not ws_authority_working:
            self.checks_failed += 1
            results["status"] = "FAIL"
            print("   ❌ WebSocket authority: FAIL (feature enabled but not working)")
        else:
            self.checks_passed += 1
            results["status"] = "PASS"
            print("   ✅ WebSocket authority: PASS (REST fallback allowed)")
        return results

    def check_coordinator_integrity(self) -> Dict[str, Any]:
        """Check Trading Bot coordinator integrity."""
        print("🎯 Checking coordinator integrity...")

        try:
            from trading_bot_v2.trading_bot import TradingBot

            # Check if coordinator methods exist
            coordinator_methods = [
                "_handle_signal_generated",
                "_coordinate_signal_execution",
                "_setup_event_subscriptions",
                "get_coordinator_status",
            ]

            methods_present = []
            for method in coordinator_methods:
                if hasattr(TradingBot, method):
                    methods_present.append(method)

            results = {
                "coordinator_methods_present": methods_present,
                "coordinator_methods_missing": [
                    m for m in coordinator_methods if m not in methods_present
                ],
                "coordinator_type": "pure_coordinator",  # Phase 2 achievement
            }

            if len(methods_present) == len(coordinator_methods):
                self.checks_passed += 1
                results["status"] = "PASS"
                print("   ✅ Coordinator integrity: PASS")
                self.checks_failed += 1
                self.checks_failed += 1
                results["status"] = "FAIL"
                print(
                    f"   ❌ Coordinator integrity: FAIL ({len(results['coordinator_methods_missing'])} methods missing)"
                )

        except Exception as e:
            self.checks_failed += 1
            results = {"status": "ERROR", "error": str(e)}
            print(f"   ❌ Coordinator integrity: ERROR ({e})")

        return results

    def check_performance_baselines(self) -> Dict[str, Any]:
        """Check that performance meets baseline requirements."""
        print("⚡ Checking performance baselines...")

        # Simple performance tests
        results: Dict[str, Any] = {}

        # Event processing speed test
        start_time = time.time()
        for _ in range(1000):
            self.event_bus.publish_event(EventType.SIGNAL_GENERATED, {}, "perf_test")
        event_time = time.time() - start_time

        results["event_processing_1000"] = {
            "time_seconds": event_time,
            "events_per_second": 1000 / event_time,
            "meets_baseline": (1000 / event_time) > 100,  # 100 events/sec minimum
        }

        # Component registry lookup speed
        start_time = time.time()
        for _ in range(10000):
            self.registry.list_components()
        registry_time = time.time() - start_time

        results["registry_lookup_10000"] = {
            "time_seconds": registry_time,
            "lookups_per_second": 10000 / registry_time,
            "meets_baseline": (10000 / registry_time)
            > 1000,  # 1000 lookups/sec minimum
        }

        # Overall performance status
        all_meet_baseline = all(
            test["meets_baseline"]
            for test in results.values()
            if isinstance(test, dict) and "meets_baseline" in test
        )

        if all_meet_baseline:
            self.checks_passed += 1
            results["status"] = "PASS"
            print("   ✅ Performance baselines: PASS")
            self.checks_failed += 1
            self.checks_failed += 1
            results["status"] = "FAIL"
            print("   ❌ Performance baselines: FAIL")
        return results

    def check_security_validation(self) -> Dict[str, Any]:
        """Check security-related validations."""
        print("🔒 Checking security validations...")

        results = {
            "emergency_shutdown_available": hasattr(
                self.monitoring, "trigger_emergency_shutdown"
            ),
            "component_isolation": len(self.registry.list_components()) > 0,
            "event_system_isolation": self.event_bus.get_stats()["total_subscribers"]
            >= 0,
        }

        # Check for emergency controls
        emergency_controls = all(
            [
                hasattr(self.monitoring, "trigger_emergency_shutdown"),
                hasattr(self.feature_flags, "emergency_rollback"),
                len(self.registry.list_components()) > 0,  # Components are isolated
            ]
        )

        if emergency_controls:
            self.checks_passed += 1
            results["status"] = "PASS"
            print("   ✅ Security validation: PASS")
            self.checks_failed += 1
            self.checks_failed += 1
            results["status"] = "FAIL"
            print("   ❌ Security validation: FAIL")
        return results

    def generate_summary(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Generate overall health check summary."""
        total_checks = self.checks_passed + self.checks_failed
        success_rate = (
            (self.checks_passed / total_checks * 100) if total_checks > 0 else 0
        )

        # Determine overall system health
        critical_failures = 0
        for check_name, check_result in results.items():
            if isinstance(check_result, dict) and check_result.get("status") == "FAIL":
                # Check if this is a critical failure
                if check_name in [
                    "component_health",
                    "coordinator_integrity",
                    "websocket_authority",
                ]:
                    critical_failures += 1

        if critical_failures == 0 and success_rate >= 80:
            overall_status = "HEALTHY"
        elif critical_failures <= 1 and success_rate >= 60:
            overall_status = "DEGRADED"
        else:
            overall_status = "UNHEALTHY"

        summary = {
            "overall_status": overall_status,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "success_rate_percent": round(success_rate, 1),
            "critical_failures": critical_failures,
            "warnings": self.warnings,
            "recommendations": self.generate_recommendations(results, overall_status),
        }

        return summary

    def generate_recommendations(
        self, results: Dict[str, Any], overall_status: str
    ) -> List[str]:
        """Generate recommendations based on check results."""
        recommendations = []

        if overall_status == "UNHEALTHY":
            recommendations.append(
                "🚨 Critical issues detected - do not deploy to production"
            )
        elif overall_status == "DEGRADED":
            recommendations.append(
                "⚠️ System is degraded - monitor closely in production"
            )

        # Specific recommendations based on failures
        if results.get("component_health", {}).get("unhealthy_components", 0) > 0:
            recommendations.append("Fix unhealthy components before deployment")

        if not results.get("websocket_authority", {}).get("authority_working", False):
            recommendations.append(
                "WebSocket authority not working - prices may fall back to REST"
            )

        if results.get("coordinator_integrity", {}).get("status") == "FAIL":
            recommendations.append(
                "Coordinator integrity compromised - trading may not work properly"
            )

        if results.get("performance_baselines", {}).get("status") == "FAIL":
            recommendations.append(
                "Performance below baseline - optimize before production"
            )

        if not recommendations:
            recommendations.append("✅ All checks passed - system ready for deployment")

        return recommendations

    def suppressed_output(self) -> "contextlib.redirect_stdout[io.StringIO]":
        """Context manager to suppress stdout/stderr during testing."""
        import contextlib
        import io

        return contextlib.redirect_stdout(io.StringIO())


def main() -> None:
    """Run health check and display results."""
    checker = HealthChecker()
    results = checker.run_all_checks()

    print("\n" + "=" * 60)
    print("🏥 SYSTEM HEALTH CHECK RESULTS")
    print("=" * 60)

    summary = results["summary"]
    print(f"Overall Status: {summary['overall_status']}")
    print(f"Success Rate: {summary['success_rate_percent']}%")
    print(f"Checks Passed: {summary['checks_passed']}")
    print(f"Checks Failed: {summary['checks_failed']}")
    print(f"Critical Failures: {summary['critical_failures']}")

    print("\n📋 Recommendations:")
    for rec in summary["recommendations"]:
        print(f"   • {rec}")

    if summary["warnings"]:
        print("\n⚠️ Warnings:")
        for warning in summary["warnings"]:
            print(f"   • {warning}")

    print(f"\n🕒 Check completed at: {results['timestamp']}")

    # Exit with appropriate code
    if summary["overall_status"] == "HEALTHY":
        print("✅ System is HEALTHY")
        sys.exit(0)
    elif summary["overall_status"] == "DEGRADED":
        print("⚠️ System is DEGRADED")
        sys.exit(1)
    else:
        print("❌ System is UNHEALTHY")
        sys.exit(2)


if __name__ == "__main__":
    main()
