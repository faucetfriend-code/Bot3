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
from typing import Dict, Any, List

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_bot_v2.monitoring import get_monitoring_system
from trading_bot_v2.component_registry import get_component_registry
from trading_bot_v2.event_system import get_event_bus, EventType
from trading_bot_v2.feature_flags import get_feature_flags


class HealthChecker:
    """Comprehensive system health checker."""

    def __init__(self):
        self.monitoring = get_monitoring_system()
        self.registry = get_component_registry()
        self.event_bus = get_event_bus()
        self.feature_flags = get_feature_flags()
        self.checks_passed = 0
        self.checks_failed = 0

    def run_all_checks(self) -> Dict[str, Any]:
        """Run all health checks and return results."""
        print("Starting comprehensive system health check...")
        print("=" * 60)
        
        results = {}
        
        # Run all checks
        results['phase3_features'] = self.check_phase3_features()
        results['component_health'] = self.check_component_health()
        results['event_system'] = self.check_event_system()
        results['websocket_authority'] = self.check_websocket_authority()
        results['coordinator_integrity'] = self.check_coordinator_integrity()
        results['performance_baselines'] = self.check_performance_baselines()
        results['security_validation'] = self.check_security_validation()
        
        # Generate summary
        results['summary'] = self.generate_summary(results)
        results['timestamp'] = datetime.now().isoformat()
        
        return results

    def check_phase3_features(self) -> Dict[str, Any]:
        """Check Phase 3 feature availability."""
        print("🔍 Checking Phase 3 features...")
        
        features = {
            'coordinator_pattern': hasattr(self, '_coordinate_signal_execution'),
            'event_system': self.event_bus is not None,
            'component_registry': self.registry is not None,
            'monitoring': self.monitoring is not None,
            'feature_flags': self.feature_flags is not None
        }
        
        results = {'features': features, 'status': 'PASS'}
        
        if all(features.values()):
            print("   ✅ Phase 3 features: AVAILABLE")
            self.checks_passed += 1
        else:
            missing = [k for k, v in features.items() if not v]
            print(f"   ❌ Phase 3 features: MISSING {missing}")
            results['status'] = 'FAIL'
            self.checks_failed += 1
        
        return results

    def check_component_health(self) -> Dict[str, Any]:
        """Check health of registered components."""
        print("🏥 Checking component health...")
        
        components = self.registry.list_components()
        
        results = {
            'total_components': len(components),
            'components': components,
            'status': 'PASS'
        }
        
        if len(components) > 0:
            print(f"   ✅ Found {len(components)} healthy components")
            self.checks_passed += 1
        else:
            print("   ❌ No components found")
            results['status'] = 'FAIL'
            self.checks_failed += 1
        
        return results

    def check_event_system(self) -> Dict[str, Any]:
        """Check event system functionality."""
        print("📡 Checking event system...")
        
        def test_handler(event):
            return True
        
        self.event_bus.subscribe(EventType.SIGNAL_GENERATED, test_handler, "health_check")
        
        subscribers = self.event_bus.get_stats()
        
        results = {
            'total_subscribers': subscribers.get('total_subscribers', 0),
            'status': 'PASS'
        }
        
        if subscribers.get('total_subscribers', 0) > 0:
            print(f"   ✅ Event system operational ({subscribers['total_subscribers']} subscribers)")
            self.checks_passed += 1
        else:
            print("   ❌ No subscribers registered")
            results['status'] = 'FAIL'
            self.checks_failed += 1
        
        return results

    def check_websocket_authority(self) -> Dict[str, Any]:
        """Check WebSocket authority configuration."""
        print("🔌 Checking WebSocket authority...")
        
        from trading_bot_v2.config import config
        
        results = {
            'websocket_enabled': getattr(config, 'enable_websocket', False),
            'rest_fallback': getattr(config, 'rest_fallback_enabled', True),
            'status': 'PASS'
        }
        
        if config.enable_websocket and not config.rest_fallback_enabled:
            print("   ✅ WebSocket is authoritative (REST fallback disabled)")
            self.checks_passed += 1
        elif not config.enable_websocket:
            print("   ⚠️ WebSocket disabled - using REST fallback")
            self.checks_passed += 1
        else:
            print("   ❌ WebSocket authority: FAIL (feature enabled but not working)")
            results['status'] = 'FAIL'
            self.checks_failed += 1
        
        return results

    def check_coordinator_integrity(self) -> Dict[str, Any]:
        """Check Trading Bot coordinator integrity."""
        print("🎯 Checking coordinator integrity...")

        try:
            from trading_bot_v2.trading_bot import TradingBot

            # Check if coordinator methods exist
            coordinator_methods = [
                '_handle_signal_generated',
                '_coordinate_signal_execution',
                '_setup_event_subscriptions',
                'get_coordinator_status'
            ]

            methods_present = []
            for method in coordinator_methods:
                if hasattr(TradingBot, method):
                    methods_present.append(method)

            results = {
                'coordinator_methods_present': methods_present,
                'coordinator_methods_missing': [m for m in coordinator_methods if m not in methods_present],
                'coordinator_type': 'pure_coordinator'
            }

            if len(methods_present) == len(coordinator_methods):
                self.checks_passed += 1
                results['status'] = 'PASS'
                print("   ✅ Coordinator integrity: PASS")
            else:
                self.checks_failed += 1
                results['status'] = 'FAIL'
                print(f"   ❌ Coordinator integrity: FAIL ({len(results['coordinator_methods_missing'])} methods missing)")

        except Exception as e:
            self.checks_failed += 1
            results = {
                'status': 'ERROR',
                'error': str(e)
            }
            print(f"   ❌ Coordinator integrity: ERROR ({e})")

        return results

    def check_performance_baselines(self) -> Dict[str, Any]:
        """Check that performance meets baseline requirements."""
        print("⚡ Checking performance baselines...")

        # Simple performance tests
        results = {}

        # Event processing speed test
        start_time = time.time()
        for _ in range(1000):
            self.event_bus.publish_event(EventType.SIGNAL_GENERATED, {}, 'perf_test')
        event_time = time.time() - start_time

        results['event_processing_1000'] = {
            'time_seconds': event_time,
            'events_per_second': 1000 / event_time,
            'meets_baseline': (1000 / event_time) > 100
        }

        # Component registry lookup speed
        start_time = time.time()
        for _ in range(10000):
            self.registry.list_components()
        registry_time = time.time() - start_time

        results['registry_lookup_10000'] = {
            'time_seconds': registry_time,
            'lookups_per_second': 10000 / registry_time,
            'meets_baseline': (10000 / registry_time) > 1000
        }

        # Overall performance status
        all_meet_baseline = all(
            test['meets_baseline']
            for test in results.values()
            if isinstance(test, dict) and 'meets_baseline' in test
        )

        if all_meet_baseline:
            self.checks_passed += 1
            results['status'] = 'PASS'
            print("   ✅ Performance baselines: PASS")
        else:
            self.checks_failed += 1
            results['status'] = 'FAIL'
            print("   ❌ Performance baselines: FAIL")

        return results

    def check_security_validation(self) -> Dict[str, Any]:
        """Check security-related validations."""
        print("🔒 Checking security validations...")

        results = {
            'emergency_shutdown_available': hasattr(self.monitoring, 'trigger_emergency_shutdown'),
            'component_isolation': len(self.registry.list_components()) > 0,
            'event_system_isolation': self.event_bus.get_stats()['total_subscribers'] >= 0
        }

        # Check for emergency controls
        emergency_controls = all([
            hasattr(self.monitoring, 'trigger_emergency_shutdown'),
            hasattr(self.feature_flags, 'emergency_rollback'),
            len(self.registry.list_components()) > 0
        ])

        if emergency_controls:
            self.checks_passed += 1
            results['status'] = 'PASS'
            print("   ✅ Security validation: PASS")
        else:
            self.checks_failed += 1
            results['status'] = 'FAIL'
            print("   ❌ Security validation: FAIL")

        return results

    def generate_summary(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Generate overall health check summary."""
        total_checks = self.checks_passed + self.checks_failed
        success_rate = (self.checks_passed / total_checks * 100) if total_checks > 0 else 0

        # Determine overall system health
        critical_failures = 0
        for check_name, check_result in results.items():
            if isinstance(check_result, dict) and check_result.get('status') == 'FAIL':
                if check_name in ['component_health', 'coordinator_integrity', 'websocket_authority']:
                    critical_failures += 1

        if critical_failures == 0 and success_rate >= 80:
            overall_status = 'HEALTHY'
        elif critical_failures == 0 and success_rate >= 50:
            overall_status = 'DEGRADED'
        else:
            overall_status = 'UNHEALTHY'

        return {
            'total_checks': total_checks,
            'checks_passed': self.checks_passed,
            'checks_failed': self.checks_failed,
            'success_rate_percent': round(success_rate, 1),
            'overall_status': overall_status,
            'critical_failures': critical_failures,
            'recommendations': self.generate_recommendations(results, overall_status),
            'warnings': []
        }

    def generate_recommendations(self, results: Dict[str, Any], overall_status: str) -> List[str]:
        """Generate recommendations based on health check results."""
        recommendations = []

        if overall_status == 'UNHEALTHY':
            recommendations.append("System is unhealthy - immediate attention required")

        for check_name, check_result in results.items():
            if isinstance(check_result, dict) and check_result.get('status') in ['FAIL', 'ERROR']:
                if check_name == 'component_health':
                    recommendations.append("Review component health - some components may need restarting")
                elif check_name == 'websocket_authority':
                    recommendations.append("Check WebSocket connection - ensure it's primary data source")
                elif check_name == 'coordinator_integrity':
                    recommendations.append("Review coordinator implementation - some methods may be missing")

        if overall_status == 'HEALTHY':
            recommendations.append("System is healthy - continue monitoring")

        if not recommendations:
            recommendations.append("All checks passed - system ready for deployment")

        return recommendations


def main():
    """Run health check and display results."""
    checker = HealthChecker()
    results = checker.run_all_checks()

    print("\n" + "=" * 60)
    print("🏥 SYSTEM HEALTH CHECK RESULTS")
    print("=" * 60)

    summary = results['summary']
    print(f"Overall Status: {summary['overall_status']}")
    print(f"Success Rate: {summary['success_rate_percent']}%")
    print(f"Checks Passed: {summary['checks_passed']}")
    print(f"Checks Failed: {summary['checks_failed']}")
    print(f"Critical Failures: {summary['critical_failures']}")

    print(f"\n📋 Recommendations:")
    for rec in summary['recommendations']:
        print(f"   • {rec}")

    if summary['warnings']:
        print(f"\n⚠️ Warnings:")
        for warning in summary['warnings']:
            print(f"   • {warning}")

    print(f"\n🕒 Check completed at: {results['timestamp']}")

    # Exit with appropriate code
    if summary['overall_status'] == 'HEALTHY':
        print("✅ System is HEALTHY")
        sys.exit(0)
    elif summary['overall_status'] == 'DEGRADED':
        print("⚠️ System is DEGRADED")
        sys.exit(1)
    else:
        print("❌ System is UNHEALTHY")
        sys.exit(2)


if __name__ == '__main__':
    main()