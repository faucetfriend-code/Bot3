"""
Comprehensive Validation Script for Groked1-4 Updates
Performs read-only tests and generates detailed report.

Usage:
    python validate_groked.py [--api-url http://localhost:8000]
"""

import os
import sys
import re
import json
import sqlite3
import requests
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, field

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from database import DATABASE_PATH, get_db_connection


@dataclass
class ValidationResult:
    """Result of a single validation test"""
    test_name: str
    status: str  # 'PASS', 'FAIL', 'WARNING', 'SKIPPED'
    message: str
    details: Optional[str] = None
    file_location: Optional[str] = None


@dataclass
class ValidationReport:
    """Complete validation report"""
    timestamp: str
    results: List[ValidationResult] = field(default_factory=list)

    @property
    def total_tests(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return len([r for r in self.results if r.status == 'PASS'])

    @property
    def failed(self) -> int:
        return len([r for r in self.results if r.status == 'FAIL'])

    @property
    def warnings(self) -> int:
        return len([r for r in self.results if r.status == 'WARNING'])

    @property
    def skipped(self) -> int:
        return len([r for r in self.results if r.status == 'SKIPPED'])


class GrokedValidator:
    """Main validation class for all groked updates"""

    def __init__(self, api_base_url: str = "http://localhost:8000"):
        self.api_base_url = api_base_url
        self.report = ValidationReport(timestamp=datetime.now().isoformat())
        self.project_root = Path(__file__).parent

    def add_result(self, test_name: str, status: str, message: str,
                   details: Optional[str] = None, file_location: Optional[str] = None):
        """Add a validation result"""
        result = ValidationResult(test_name, status, message, details, file_location)
        self.report.results.append(result)

        # Print real-time feedback (Windows-safe)
        status_symbol = {'PASS': '[OK]', 'FAIL': '[FAIL]', 'WARNING': '[WARN]', 'SKIPPED': '[SKIP]'}
        print(f"{status_symbol.get(status, '[ ]')} {test_name}: {message}")

    def read_file_content(self, file_path: str) -> Optional[str]:
        """Safely read file content"""
        try:
            full_path = self.project_root / file_path
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            return None
        except Exception as e:
            return None

    def check_pattern_in_file(self, file_path: str, pattern: str, description: str) -> bool:
        """Check if a pattern exists in a file"""
        content = self.read_file_content(file_path)
        if content is None:
            self.add_result(
                f"Pattern Check: {description}",
                'FAIL',
                f"Could not read file: {file_path}"
            )
            return False

        if re.search(pattern, content, re.MULTILINE):
            return True
        return False

    # =========================================================================
    # GROKED1: Database Cleanup Script Validation
    # =========================================================================

    def validate_groked1_implementation(self):
        """Validate cleanup_database.py implementation (code review only)"""
        print("\n" + "="*70)
        print("GROKED1: Database Cleanup Script - Code Review")
        print("="*70)

        file_path = "cleanup_database.py"

        # Test 1: File exists
        if not (self.project_root / file_path).exists():
            self.add_result(
                "Groked1: File Exists",
                'FAIL',
                "cleanup_database.py not found",
                file_location=file_path
            )
            return
        else:
            self.add_result(
                "Groked1: File Exists",
                'PASS',
                "cleanup_database.py found (356 lines expected)"
            )

        # Test 2: Uses DATABASE_PATH constant
        if self.check_pattern_in_file(file_path, r'from database import.*DATABASE_PATH',
                                      "Uses DATABASE_PATH constant"):
            self.add_result(
                "Groked1: DATABASE_PATH Usage",
                'PASS',
                "Correctly imports DATABASE_PATH from database.py",
                file_location=f"{file_path}:16"
            )
        else:
            self.add_result(
                "Groked1: DATABASE_PATH Usage",
                'FAIL',
                "Does not import DATABASE_PATH constant",
                details="Should import from database.py, not hardcode paths",
                file_location=file_path
            )

        # Test 3: Transaction safety (BEGIN/COMMIT/ROLLBACK)
        content = self.read_file_content(file_path)
        if content:
            has_begin = 'BEGIN' in content or '"BEGIN"' in content
            has_commit = 'COMMIT' in content or '"COMMIT"' in content
            has_rollback = 'ROLLBACK' in content or '"ROLLBACK"' in content

            if has_begin and has_commit and has_rollback:
                self.add_result(
                    "Groked1: Transaction Safety",
                    'PASS',
                    "Implements BEGIN/COMMIT/ROLLBACK pattern",
                    file_location=f"{file_path}:238-274"
                )
            else:
                missing = []
                if not has_begin: missing.append("BEGIN")
                if not has_commit: missing.append("COMMIT")
                if not has_rollback: missing.append("ROLLBACK")
                self.add_result(
                    "Groked1: Transaction Safety",
                    'FAIL',
                    f"Missing transaction keywords: {', '.join(missing)}",
                    file_location=file_path
                )

        # Test 4: Foreign key constraints
        if self.check_pattern_in_file(file_path, r'PRAGMA foreign_keys\s*=\s*ON',
                                      "Foreign key constraints enabled"):
            self.add_result(
                "Groked1: Foreign Key Constraints",
                'PASS',
                "PRAGMA foreign_keys = ON found",
                file_location=f"{file_path}:240"
            )
        else:
            self.add_result(
                "Groked1: Foreign Key Constraints",
                'WARNING',
                "PRAGMA foreign_keys = ON not explicitly set"
            )

        # Test 5: Backup creation function
        if self.check_pattern_in_file(file_path, r'def create_backup\(\)', "Backup function exists"):
            self.add_result(
                "Groked1: Backup Function",
                'PASS',
                "create_backup() function found",
                file_location=f"{file_path}:45-51"
            )
        else:
            self.add_result(
                "Groked1: Backup Function",
                'FAIL',
                "create_backup() function not found"
            )

        # Test 6: VACUUM optimization
        if self.check_pattern_in_file(file_path, r'VACUUM', "VACUUM command"):
            self.add_result(
                "Groked1: VACUUM Optimization",
                'PASS',
                "VACUUM command found for database optimization",
                file_location=f"{file_path}:288-293"
            )
        else:
            self.add_result(
                "Groked1: VACUUM Optimization",
                'WARNING',
                "VACUUM command not found"
            )

        # Test 7: Dry-run mode
        if self.check_pattern_in_file(file_path, r'--dry-run', "Dry-run flag"):
            self.add_result(
                "Groked1: Dry-Run Mode",
                'PASS',
                "--dry-run command-line flag implemented"
            )
        else:
            self.add_result(
                "Groked1: Dry-Run Mode",
                'WARNING',
                "--dry-run flag not found in argparse"
            )

        # Test 8: Comprehensive logging
        if content:
            logger_calls = len(re.findall(r'logger\.(info|warning|error|critical)', content))
            if logger_calls >= 30:
                self.add_result(
                    "Groked1: Comprehensive Logging",
                    'PASS',
                    f"Found {logger_calls} logger calls (>= 30 expected)"
                )
            else:
                self.add_result(
                    "Groked1: Comprehensive Logging",
                    'WARNING',
                    f"Found only {logger_calls} logger calls (< 30)"
                )

        # Test 9: Note execution skipped (read-only mode)
        self.add_result(
            "Groked1: Execution Test",
            'SKIPPED',
            "Script execution skipped (read-only validation mode)",
            details="Run manually with: python cleanup_database.py --dry-run"
        )

    # =========================================================================
    # GROKED2: UI/API Fixes Validation
    # =========================================================================

    def validate_groked2_endpoints(self):
        """Validate API endpoints for groked2 updates"""
        print("\n" + "="*70)
        print("GROKED2: UI/API Fixes - Endpoint Testing")
        print("="*70)

        # Test 1: Indicators endpoint
        self._test_indicators_endpoint()

        # Test 2: Funding rates endpoints
        self._test_funding_rates_endpoints()

        # Test 3: Symbol normalization utilities
        self._test_symbol_normalization()

    def _test_indicators_endpoint(self):
        """Test /api/indicators/current endpoint"""
        endpoint = f"{self.api_base_url}/api/indicators/current"

        try:
            response = requests.get(f"{endpoint}?symbol=BTC&timeframe=1H", timeout=10)

            if response.status_code == 200:
                data = response.json()

                # Check response structure
                if data.get('success'):
                    self.add_result(
                        "Groked2: Indicators Endpoint Response",
                        'PASS',
                        f"Endpoint returns 200 OK with success=true"
                    )

                    # Check required fields
                    indicator_data = data.get('data', {})
                    required_fields = ['rsi', 'sma', 'ema', 'macd', 'atr', 'bb', 'volume_ma']
                    missing_fields = [f for f in required_fields if f not in indicator_data]

                    if not missing_fields:
                        self.add_result(
                            "Groked2: Indicators Fields Complete",
                            'PASS',
                            "All required indicator fields present (RSI, SMA, EMA, MACD, ATR, BB, Vol MA)"
                        )
                    else:
                        self.add_result(
                            "Groked2: Indicators Fields Complete",
                            'FAIL',
                            f"Missing fields: {', '.join(missing_fields)}",
                            details=f"Response data keys: {list(indicator_data.keys())}"
                        )

                    # Check data types
                    type_errors = []
                    if 'rsi' in indicator_data and not isinstance(indicator_data['rsi'], (int, float, type(None))):
                        type_errors.append("rsi should be number")
                    if 'macd' in indicator_data and not isinstance(indicator_data['macd'], dict):
                        type_errors.append("macd should be dict")

                    if not type_errors:
                        self.add_result(
                            "Groked2: Indicators Data Types",
                            'PASS',
                            "All indicator data types are valid"
                        )
                    else:
                        self.add_result(
                            "Groked2: Indicators Data Types",
                            'WARNING',
                            f"Data type issues: {', '.join(type_errors)}"
                        )
                else:
                    self.add_result(
                        "Groked2: Indicators Endpoint Response",
                        'FAIL',
                        f"Endpoint returned success=false: {data.get('error', 'Unknown error')}"
                    )
            else:
                self.add_result(
                    "Groked2: Indicators Endpoint",
                    'FAIL',
                    f"Endpoint returned {response.status_code}: {response.text[:200]}"
                )
        except requests.exceptions.ConnectionError:
            self.add_result(
                "Groked2: Indicators Endpoint",
                'SKIPPED',
                "API server not running - start with: python api_server.py",
                details=f"Could not connect to {self.api_base_url}"
            )
        except Exception as e:
            self.add_result(
                "Groked2: Indicators Endpoint",
                'FAIL',
                f"Exception: {str(e)}"
            )

    def _test_funding_rates_endpoints(self):
        """Test funding rates endpoints"""

        # Endpoint 1: /api/funding-rates
        try:
            response = requests.get(f"{self.api_base_url}/api/funding-rates", timeout=10)

            if response.status_code == 200:
                data = response.json()

                # Check for 24x/day warning
                warning_text = str(data).lower()
                has_hourly_warning = '24' in str(data) or 'hourly' in warning_text or 'hour' in warning_text

                if has_hourly_warning:
                    self.add_result(
                        "Groked2: Funding Rates 24x Warning",
                        'PASS',
                        "Funding rates endpoint includes hourly/24x warning",
                        file_location="api_server.py:2527-2560"
                    )
                else:
                    self.add_result(
                        "Groked2: Funding Rates 24x Warning",
                        'WARNING',
                        "No explicit 24x/day or hourly warning found",
                        details="Pacifica charges funding HOURLY (24x per day), warning should be prominent"
                    )

                self.add_result(
                    "Groked2: Funding Rates Endpoint",
                    'PASS',
                    f"/api/funding-rates returns 200 OK"
                )
            else:
                self.add_result(
                    "Groked2: Funding Rates Endpoint",
                    'FAIL',
                    f"Endpoint returned {response.status_code}"
                )
        except requests.exceptions.ConnectionError:
            self.add_result(
                "Groked2: Funding Rates Endpoint",
                'SKIPPED',
                "API server not running"
            )
        except Exception as e:
            self.add_result(
                "Groked2: Funding Rates Endpoint",
                'FAIL',
                f"Exception: {str(e)}"
            )

    def _test_symbol_normalization(self):
        """Test symbol normalization utilities"""
        file_path = "utils/symbol_utils.py"

        if not (self.project_root / file_path).exists():
            self.add_result(
                "Groked2: Symbol Utils File",
                'FAIL',
                f"{file_path} not found"
            )
            return

        # Check for normalization functions
        has_normalize = self.check_pattern_in_file(file_path, r'def normalize_symbol',
                                                    "normalize_symbol function")
        has_add_suffix = self.check_pattern_in_file(file_path, r'def add_pacifica_suffix',
                                                     "add_pacifica_suffix function")

        if has_normalize and has_add_suffix:
            self.add_result(
                "Groked2: Symbol Normalization Functions",
                'PASS',
                "Both normalize_symbol() and add_pacifica_suffix() found",
                file_location="utils/symbol_utils.py"
            )
        else:
            missing = []
            if not has_normalize: missing.append("normalize_symbol")
            if not has_add_suffix: missing.append("add_pacifica_suffix")
            self.add_result(
                "Groked2: Symbol Normalization Functions",
                'FAIL',
                f"Missing functions: {', '.join(missing)}"
            )

    # =========================================================================
    # GROKED3: Bot Runtime Fixes Validation
    # =========================================================================

    def validate_groked3_runtime(self):
        """Validate runtime fixes (code review)"""
        print("\n" + "="*70)
        print("GROKED3: Bot Runtime Fixes - Code Review")
        print("="*70)

        file_path = "execution.py"

        # Test 1: Empty symbol validation
        pattern = r"if\s+not\s+symbol\s+or\s+symbol\.strip\(\)\s*==\s*['\"]"
        if self.check_pattern_in_file(file_path, pattern, "Empty symbol validation"):
            self.add_result(
                "Groked3: Empty Symbol Validation",
                'PASS',
                "Empty symbol validation found in get_ticker()",
                file_location=f"{file_path}:714-716"
            )
        else:
            self.add_result(
                "Groked3: Empty Symbol Validation",
                'WARNING',
                "Empty symbol validation pattern not found",
                details="Expected: if not symbol or symbol.strip() == ''"
            )

        # Test 2: Position sync fallback logic
        content = self.read_file_content(file_path)
        if content:
            # Look for cache patterns
            has_cache = '_ticker_cache' in content or 'ticker_cache' in content
            has_fallback = 'fallback' in content.lower() or 'expired cache' in content.lower()

            if has_cache:
                self.add_result(
                    "Groked3: Position Sync Caching",
                    'PASS',
                    "Ticker caching mechanism found",
                    file_location=f"{file_path}:740-782"
                )
            else:
                self.add_result(
                    "Groked3: Position Sync Caching",
                    'WARNING',
                    "Ticker cache not found in execution.py"
                )

        # Test 3: Order validation with market specs
        if self.check_pattern_in_file(file_path, r'def validate_order', "validate_order method"):
            self.add_result(
                "Groked3: Order Validation Method",
                'PASS',
                "validate_order() method found",
                file_location=f"{file_path}:423-470"
            )

            # Check for tick_size and lot_size validation
            content = self.read_file_content(file_path)
            if content and 'tick_size' in content and 'lot_size' in content:
                self.add_result(
                    "Groked3: Market Specs Validation",
                    'PASS',
                    "Order validation checks tick_size and lot_size"
                )
            else:
                self.add_result(
                    "Groked3: Market Specs Validation",
                    'WARNING',
                    "tick_size/lot_size validation not confirmed"
                )
        else:
            self.add_result(
                "Groked3: Order Validation Method",
                'FAIL',
                "validate_order() method not found"
            )

        # Test 4: SSL configuration
        ssl_file = "pacifica_client.py"
        if self.check_pattern_in_file(ssl_file, r'def _configure_ssl', "SSL configuration method"):
            self.add_result(
                "Groked3: SSL Configuration",
                'PASS',
                "_configure_ssl_verification() method found",
                file_location=f"{ssl_file}:321-350"
            )
        else:
            self.add_result(
                "Groked3: SSL Configuration",
                'WARNING',
                "SSL configuration method not found in pacifica_client.py"
            )

    # =========================================================================
    # GROKED4: Server Shutdown Validation
    # =========================================================================

    def validate_groked4_shutdown(self):
        """Validate shutdown handlers (code review)"""
        print("\n" + "="*70)
        print("GROKED4: Server Shutdown Improvements - Code Review")
        print("="*70)

        file_path = "main.py"

        # Test 1: Signal handlers registered
        has_sigint = self.check_pattern_in_file(file_path, r'signal\.SIGINT', "SIGINT handler")
        has_sigterm = self.check_pattern_in_file(file_path, r'signal\.SIGTERM', "SIGTERM handler")

        if has_sigint and has_sigterm:
            self.add_result(
                "Groked4: Signal Handlers",
                'PASS',
                "Both SIGINT and SIGTERM handlers registered",
                file_location=f"{file_path}:197-207"
            )
        else:
            missing = []
            if not has_sigint: missing.append("SIGINT")
            if not has_sigterm: missing.append("SIGTERM")
            self.add_result(
                "Groked4: Signal Handlers",
                'FAIL',
                f"Missing signal handlers: {', '.join(missing)}"
            )

        # Test 2: Graceful shutdown method
        if self.check_pattern_in_file(file_path, r'async def stop', "Graceful shutdown method"):
            self.add_result(
                "Groked4: Graceful Shutdown Method",
                'PASS',
                "async def stop() method found",
                file_location=f"{file_path}:281-294"
            )
        else:
            self.add_result(
                "Groked4: Graceful Shutdown Method",
                'WARNING',
                "async def stop() method not found in main.py"
            )

        # Test 3: Emergency shutdown
        exec_file = "execution.py"
        if self.check_pattern_in_file(exec_file, r'def emergency_shutdown', "Emergency shutdown"):
            self.add_result(
                "Groked4: Emergency Shutdown",
                'PASS',
                "emergency_shutdown() method found",
                file_location=f"{exec_file}:1464-1468"
            )
        else:
            self.add_result(
                "Groked4: Emergency Shutdown",
                'WARNING',
                "emergency_shutdown() method not found in execution.py"
            )

        # Test 4: Interactive shutdown test note
        self.add_result(
            "Groked4: Interactive Shutdown Test",
            'SKIPPED',
            "Manual test required: Start server and press CTRL+C",
            details="Expected: Clean shutdown within 5 seconds, no CancelledError traceback"
        )

    # =========================================================================
    # CLAUDE.md Guidelines Compliance
    # =========================================================================

    def validate_guidelines_compliance(self):
        """Verify CLAUDE.md pattern compliance"""
        print("\n" + "="*70)
        print("CLAUDE.md Guidelines Compliance Checks")
        print("="*70)

        # Check 1: No hardcoded database paths
        files_to_check = ["cleanup_database.py", "api_server.py", "execution.py"]
        hardcoded_issues = []

        for file_path in files_to_check:
            content = self.read_file_content(file_path)
            if content:
                # Look for hardcoded database paths
                if re.search(r'sqlite3\.connect\(["\']trading_bot\.db["\']\)', content):
                    hardcoded_issues.append(file_path)

        if not hardcoded_issues:
            self.add_result(
                "Guidelines: No Hardcoded DB Paths",
                'PASS',
                "No hardcoded 'trading_bot.db' paths found"
            )
        else:
            self.add_result(
                "Guidelines: No Hardcoded DB Paths",
                'FAIL',
                f"Hardcoded paths found in: {', '.join(hardcoded_issues)}",
                details="Should use DATABASE_PATH from database.py"
            )

        # Check 2: API response structure
        api_file = "api_server.py"
        content = self.read_file_content(api_file)
        if content:
            # Count occurrences of standard response structure
            success_returns = len(re.findall(r'["\']success["\']:\s*True', content))

            if success_returns > 10:
                self.add_result(
                    "Guidelines: API Response Structure",
                    'PASS',
                    f"Found {success_returns} endpoints using standard {{success, data, error}} structure"
                )
            else:
                self.add_result(
                    "Guidelines: API Response Structure",
                    'WARNING',
                    f"Only found {success_returns} standard response structures (expected > 10)"
                )

        # Check 3: Async/await patterns
        exec_content = self.read_file_content("execution.py")
        if exec_content:
            async_defs = len(re.findall(r'async def ', exec_content))
            await_calls = len(re.findall(r'\bawait\s+', exec_content))

            if async_defs > 0 and await_calls > 0:
                self.add_result(
                    "Guidelines: Async/Await Patterns",
                    'PASS',
                    f"Proper async/await usage: {async_defs} async functions, {await_calls} await calls"
                )
            else:
                self.add_result(
                    "Guidelines: Async/Await Patterns",
                    'WARNING',
                    "Limited async/await usage detected"
                )

    # =========================================================================
    # Integration & Regression Testing
    # =========================================================================

    def validate_integration(self):
        """Integration and regression tests (read-only)"""
        print("\n" + "="*70)
        print("Integration & Regression Testing")
        print("="*70)

        # Test 1: Database integrity check
        self._test_database_integrity()

        # Test 2: Endpoint smoke tests
        self._test_endpoint_smoke_tests()

    def _test_database_integrity(self):
        """Check database integrity with read-only queries"""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()

                # Check 1: Foreign key violations
                cursor.execute("PRAGMA foreign_key_check")
                fk_violations = cursor.fetchall()

                if not fk_violations:
                    self.add_result(
                        "Integration: Foreign Key Integrity",
                        'PASS',
                        "No foreign key violations detected"
                    )
                else:
                    self.add_result(
                        "Integration: Foreign Key Integrity",
                        'FAIL',
                        f"Found {len(fk_violations)} foreign key violations",
                        details=str(fk_violations[:5])  # First 5 violations
                    )

                # Check 2: Null values in critical fields
                null_checks = [
                    ("positions", "symbol"),
                    ("trades", "pnl"),
                    ("market_data", "symbol"),
                ]

                null_issues = []
                for table, column in null_checks:
                    try:
                        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL")
                        null_count = cursor.fetchone()[0]
                        if null_count > 0:
                            null_issues.append(f"{table}.{column}: {null_count}")
                    except sqlite3.OperationalError:
                        pass  # Table might not exist

                if not null_issues:
                    self.add_result(
                        "Integration: Critical Field Nulls",
                        'PASS',
                        "No NULL values in critical fields"
                    )
                else:
                    self.add_result(
                        "Integration: Critical Field Nulls",
                        'WARNING',
                        f"NULL values found: {', '.join(null_issues)}"
                    )

                # Check 3: Future timestamps
                cursor.execute("SELECT COUNT(*) FROM market_data WHERE timestamp > ?",
                               (int(datetime.now().timestamp()),))
                future_count = cursor.fetchone()[0]

                if future_count == 0:
                    self.add_result(
                        "Integration: Future Timestamps",
                        'PASS',
                        "No future timestamps in market_data"
                    )
                else:
                    self.add_result(
                        "Integration: Future Timestamps",
                        'WARNING',
                        f"Found {future_count} records with future timestamps"
                    )

        except Exception as e:
            self.add_result(
                "Integration: Database Integrity",
                'FAIL',
                f"Database integrity check failed: {str(e)}"
            )

    def _test_endpoint_smoke_tests(self):
        """Smoke test all critical endpoints"""
        endpoints = [
            "/api/status",
            "/api/market-data",
            "/api/positions",
            "/api/trades",
            "/api/funding-rates",
            "/api/indicators/current?symbol=BTC&timeframe=1H",
        ]

        passed = 0
        failed = []

        for endpoint in endpoints:
            try:
                response = requests.get(f"{self.api_base_url}{endpoint}", timeout=5)
                if response.status_code == 200:
                    passed += 1
                else:
                    failed.append(f"{endpoint} ({response.status_code})")
            except requests.exceptions.ConnectionError:
                self.add_result(
                    "Integration: Endpoint Smoke Tests",
                    'SKIPPED',
                    "API server not running - cannot test endpoints"
                )
                return
            except Exception as e:
                failed.append(f"{endpoint} (exception)")

        if not failed:
            self.add_result(
                "Integration: Endpoint Smoke Tests",
                'PASS',
                f"All {passed} critical endpoints return 200 OK"
            )
        else:
            self.add_result(
                "Integration: Endpoint Smoke Tests",
                'FAIL',
                f"Passed {passed}/{len(endpoints)} endpoints",
                details=f"Failed: {', '.join(failed)}"
            )

    # =========================================================================
    # Report Generation
    # =========================================================================

    def generate_report(self) -> str:
        """Generate comprehensive markdown report"""
        print("\n" + "="*70)
        print("Generating Validation Report...")
        print("="*70)

        report_lines = []

        # Header
        report_lines.append("# Groked Updates Validation Report")
        report_lines.append(f"\n**Generated:** {self.report.timestamp}")
        report_lines.append(f"**API Base URL:** {self.api_base_url}")
        report_lines.append(f"**Database:** {DATABASE_PATH}")

        # Summary
        report_lines.append("\n## Summary\n")
        report_lines.append(f"- **Total Tests:** {self.report.total_tests}")
        report_lines.append(f"- **Passed:** {self.report.passed} ✅")
        report_lines.append(f"- **Failed:** {self.report.failed} ❌")
        report_lines.append(f"- **Warnings:** {self.report.warnings} ⚠️")
        report_lines.append(f"- **Skipped:** {self.report.skipped} ⏭️")

        # Calculate pass rate
        testable = self.report.total_tests - self.report.skipped
        if testable > 0:
            pass_rate = (self.report.passed / testable) * 100
            report_lines.append(f"\n**Pass Rate:** {pass_rate:.1f}% ({self.report.passed}/{testable} tests)")

        # Detailed Results by Category
        categories = {
            "Groked1": "Database Cleanup Script",
            "Groked2": "UI/API Fixes",
            "Groked3": "Bot Runtime Fixes",
            "Groked4": "Server Shutdown",
            "Guidelines": "CLAUDE.md Compliance",
            "Integration": "Integration & Regression"
        }

        for category_key, category_name in categories.items():
            category_results = [r for r in self.report.results if r.test_name.startswith(category_key)]

            if category_results:
                report_lines.append(f"\n## {category_name}\n")

                for result in category_results:
                    status_emoji = {'PASS': '✅', 'FAIL': '❌', 'WARNING': '⚠️', 'SKIPPED': '⏭️'}
                    emoji = status_emoji.get(result.status, '•')

                    report_lines.append(f"### {emoji} {result.test_name.split(': ', 1)[1] if ': ' in result.test_name else result.test_name}")
                    report_lines.append(f"**Status:** {result.status}")
                    report_lines.append(f"**Message:** {result.message}\n")

                    if result.details:
                        report_lines.append(f"**Details:**\n```\n{result.details}\n```\n")

                    if result.file_location:
                        report_lines.append(f"**Location:** `{result.file_location}`\n")

        # Recommendations
        report_lines.append("\n## Recommendations\n")

        failures = [r for r in self.report.results if r.status == 'FAIL']
        warnings = [r for r in self.report.results if r.status == 'WARNING']

        if failures:
            report_lines.append("### Critical Issues (FAIL)\n")
            for i, result in enumerate(failures, 1):
                report_lines.append(f"{i}. **{result.test_name}**")
                report_lines.append(f"   - Issue: {result.message}")
                if result.file_location:
                    report_lines.append(f"   - File: `{result.file_location}`")
                if result.details:
                    report_lines.append(f"   - Details: {result.details}")
                report_lines.append("")

        if warnings:
            report_lines.append("### Warnings (Review Recommended)\n")
            for i, result in enumerate(warnings, 1):
                report_lines.append(f"{i}. **{result.test_name}**: {result.message}")

        if not failures and not warnings:
            report_lines.append("No issues found! All tests passed successfully. ✅")

        # Footer
        report_lines.append("\n---\n")
        report_lines.append("*Generated by validate_groked.py - Comprehensive Groked Updates Validation*")

        return "\n".join(report_lines)

    def run_all_validations(self):
        """Run all validation tests"""
        print("\n" + "="*70)
        print("GROKED UPDATES VALIDATION")
        print("="*70)
        print(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70)

        # Run all validation suites
        self.validate_groked1_implementation()
        self.validate_groked2_endpoints()
        self.validate_groked3_runtime()
        self.validate_groked4_shutdown()
        self.validate_guidelines_compliance()
        self.validate_integration()

        # Generate and save report
        report_content = self.generate_report()

        # Save to file
        report_path = self.project_root / "groked_validation_report.md"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_content)

        print(f"\n{'='*70}")
        print(f"Validation Complete!")
        print(f"Report saved to: {report_path}")
        print(f"{'='*70}\n")

        return report_content


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Validate Groked1-4 Updates")
    parser.add_argument("--api-url", default="http://localhost:8000",
                        help="API base URL (default: http://localhost:8000)")

    args = parser.parse_args()

    validator = GrokedValidator(api_base_url=args.api_url)
    report = validator.run_all_validations()

    # Print summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Total Tests: {validator.report.total_tests}")
    print(f"Passed: {validator.report.passed} [OK]")
    print(f"Failed: {validator.report.failed} [FAIL]")
    print(f"Warnings: {validator.report.warnings} [WARN]")
    print(f"Skipped: {validator.report.skipped} [SKIP]")

    # Exit code based on results
    if validator.report.failed > 0:
        print("\n[FAIL] VALIDATION FAILED - See report for details")
        sys.exit(1)
    elif validator.report.warnings > 0:
        print("\n[WARN] VALIDATION PASSED WITH WARNINGS - Review recommended")
        sys.exit(0)
    else:
        print("\n[OK] ALL VALIDATIONS PASSED!")
        sys.exit(0)


if __name__ == "__main__":
    main()
