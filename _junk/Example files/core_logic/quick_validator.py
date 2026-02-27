#!/usr/bin/env python3
"""
Quick Error Finder - Finds common errors without requiring LangChain

This is a simplified validator that runs immediately while packages install.
"""

import re
import os
from pathlib import Path
from typing import List, Dict, Set, Tuple

def find_api_mismatches() -> List[str]:
    """Find API endpoint mismatches between frontend and backend."""
    issues = []

    # Read backend
    backend_path = Path("api_server.py")
    if not backend_path.exists():
        return ["❌ api_server.py not found"]

    with open(backend_path, 'r', encoding='utf-8') as f:
        backend = f.read()

    # Read frontend
    frontend_path = Path("trading_bot_interface.html")
    if not frontend_path.exists():
        return ["❌ trading_bot_interface.html not found"]

    with open(frontend_path, 'r', encoding='utf-8') as f:
        frontend = f.read()

    # Extract backend routes
    backend_routes = set()
    for match in re.finditer(r'@app\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']\)', backend):
        method, path = match.groups()
        backend_routes.add(path)

    # Extract frontend calls
    frontend_calls = set()
    for match in re.finditer(r'(?:fetch|safeApiCall)\(["`\']([^"`\']+)["`\']', frontend):
        path = match.group(1)
        if not path.startswith('$') and not path.startswith('http'):
            # Remove ${API_BASE} prefix
            path = path.replace('${API_BASE}', '').replace('`', '')
            if path.startswith('/'):
                frontend_calls.add(path)

    # Find mismatches
    missing_in_backend = frontend_calls - backend_routes

    if missing_in_backend:
        issues.append("🔴 CRITICAL: Frontend calls endpoints that don't exist in backend:")
        for endpoint in sorted(missing_in_backend):
            issues.append(f"  ❌ {endpoint}")

    return issues

def check_javascript_errors() -> List[str]:
    """Find JavaScript errors in HTML."""
    issues = []

    html_path = Path("trading_bot_interface.html")
    if not html_path.exists():
        return ["❌ trading_bot_interface.html not found"]

    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check for common patterns
    # 1. Functions called without this. prefix in object methods
    if 'displayCurrentIndicators(' in content and 'this.displayCurrentIndicators' not in content:
        issues.append("🟠 Possible missing 'this.' prefix for displayCurrentIndicators")

    # 2. onclick handlers calling non-existent global functions
    onclick_pattern = r'onclick="([a-zA-Z_][a-zA-Z0-9_]*)\('
    for match in re.finditer(onclick_pattern, content):
        func_name = match.group(1)
        # Check if it's defined as global or as TradingBotUI method
        if f'function {func_name}(' not in content and f'{func_name}: function' not in content:
            if f'TradingBotUI.{func_name}' not in content:
                issues.append(f"🟡 onclick calls undefined function: {func_name}()")

    return issues

def check_security_issues() -> List[str]:
    """Find security issues in code."""
    issues = []

    # Check Python files
    for py_file in Path('.').glob('*.py'):
        with open(py_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check for SQL injection
        if 'execute(' in content or 'executemany(' in content:
            if 'f"' in content or "f'" in content:
                if 'SELECT' in content.upper() or 'INSERT' in content.upper():
                    issues.append(f"🔴 CRITICAL: Possible SQL injection in {py_file.name}")

        # Check for hardcoded secrets
        secret_keywords = ['password', 'api_key', 'secret', 'token']
        for keyword in secret_keywords:
            if re.search(rf'{keyword}\s*=\s*["\'][^"\']+["\']', content, re.IGNORECASE):
                # Skip if it's just a variable name or placeholder
                if 'your-' not in content.lower() and 'test-' not in content.lower():
                    issues.append(f"🔴 CRITICAL: Possible hardcoded {keyword} in {py_file.name}")

        # Check for eval/exec
        if 'eval(' in content or 'exec(' in content:
            issues.append(f"🔴 CRITICAL: Dangerous eval/exec usage in {py_file.name}")

    return issues

def check_type_hints() -> List[str]:
    """Check for missing type hints in main files."""
    issues = []

    key_files = ['api_server.py', 'main.py', 'risk.py', 'strategy.py']

    for filename in key_files:
        filepath = Path(filename)
        if not filepath.exists():
            continue

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Count functions with and without type hints
        func_pattern = r'def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)\s*(?:->)?'
        functions = re.findall(func_pattern, content)

        funcs_with_hints = len(re.findall(r'def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\([^)]*\)\s*->', content))
        total_funcs = len(functions)

        if total_funcs > 0:
            coverage = (funcs_with_hints / total_funcs) * 100
            if coverage < 80:
                issues.append(f"🟡 Type hint coverage in {filename}: {coverage:.1f}% ({funcs_with_hints}/{total_funcs})")

    return issues

def check_config_consistency() -> List[str]:
    """Check configuration consistency."""
    issues = []

    # Check .env vs .env.example
    env_path = Path('.env')
    example_path = Path('.env.example')

    if not env_path.exists():
        issues.append("🔴 CRITICAL: .env file not found")
        return issues

    if not example_path.exists():
        issues.append("🟠 .env.example not found")
        return issues

    # Parse both files
    def parse_env(path):
        vars_set = set()
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    var_name = line.split('=')[0].strip()
                    vars_set.add(var_name)
        return vars_set

    env_vars = parse_env(env_path)
    example_vars = parse_env(example_path)

    # Find differences
    in_env_not_example = env_vars - example_vars

    if in_env_not_example:
        issues.append("🟠 Variables in .env but missing from .env.example:")
        for var in sorted(in_env_not_example):
            issues.append(f"  - {var}")

    return issues

def check_error_handling() -> List[str]:
    """Check for missing error handling."""
    issues = []

    key_files = ['api_server.py', 'main.py', 'risk.py']

    for filename in key_files:
        filepath = Path(filename)
        if not filepath.exists():
            continue

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Count try-except blocks
        try_count = content.count('try:')

        # Count risky operations (should be in try-except)
        risky_ops = (
            content.count('.execute(') +
            content.count('.executemany(') +
            content.count('open(') +
            content.count('json.loads(') +
            content.count('float(') +
            content.count('int(')
        )

        if risky_ops > try_count * 3:  # Rough heuristic
            issues.append(f"🟡 {filename}: Many risky operations ({risky_ops}) but few try-except blocks ({try_count})")

    return issues

def main():
    """Run all checks."""
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("=" * 70)
    print("QUICK ERROR FINDER - Trading Bot Analysis")
    print("=" * 70)
    print()

    all_issues = []

    # Run all checks
    checks = [
        ("API Endpoint Mismatches", find_api_mismatches),
        ("JavaScript Errors", check_javascript_errors),
        ("Security Issues", check_security_issues),
        ("Type Hint Coverage", check_type_hints),
        ("Configuration Consistency", check_config_consistency),
        ("Error Handling", check_error_handling),
    ]

    for check_name, check_func in checks:
        print(f"📋 {check_name}")
        print("-" * 70)

        try:
            issues = check_func()
            if issues:
                for issue in issues:
                    print(f"  {issue}")
                    all_issues.append((check_name, issue))
            else:
                print("  ✅ No issues found")
        except Exception as e:
            print(f"  ⚠️  Error running check: {str(e)}")

        print()

    # Summary
    print("=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)

    critical = len([i for _, i in all_issues if '🔴 CRITICAL' in i])
    high = len([i for _, i in all_issues if '🟠' in i])
    medium = len([i for _, i in all_issues if '🟡' in i])

    print(f"  🔴 Critical Issues: {critical}")
    print(f"  🟠 High Priority: {high}")
    print(f"  🟡 Medium Priority: {medium}")
    print(f"  📝 Total Issues: {len(all_issues)}")
    print()

    if all_issues:
        print("💡 Recommendation: Address critical issues first, then work through high/medium priority items.")
    else:
        print("✅ No major issues found! Your codebase looks good.")

    print()
    print("=" * 70)
    print("Note: This is a quick analysis. Run full agents for comprehensive checks:")
    print("  python agents/api_validator.py -b api_server.py -f trading_bot_interface.html")
    print("  python agents/code_reviewer.py --file api_server.py --severity high")
    print("  python agents/config_manager.py --validate-all")
    print("=" * 70)

if __name__ == "__main__":
    main()
