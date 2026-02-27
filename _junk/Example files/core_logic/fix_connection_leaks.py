"""
Automated script to fix database connection leaks in api_server.py.

This script converts manual connection management patterns:
    conn = get_db_connection()
    # ... code ...
    conn.close()

To context manager patterns:
    with get_db_connection() as conn:
        # ... code ...
        # Connection auto-closes here
"""

import re
from pathlib import Path


def fix_connection_leaks(file_path: str) -> tuple[int, list[str]]:
    """
    Fix all connection leaks in the specified file.

    Returns:
        tuple: (number of fixes applied, list of line numbers fixed)
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    fixes_applied = 0
    fixed_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Look for pattern: conn = get_db_connection()
        if 'conn = get_db_connection()' in line and 'with' not in line:
            # Found a potential leak - get the indentation
            indent = len(line) - len(line.lstrip())
            indent_str = ' ' * indent

            # Replace with context manager
            lines[i] = f"{indent_str}with get_db_connection() as conn:  # ✅ Context manager\n"

            # Find the corresponding conn.close() and remove it
            # Also increase indentation for all lines in between
            j = i + 1
            found_close = False

            while j < len(lines):
                current_line = lines[j]
                current_indent = len(current_line) - len(current_line.lstrip())

                # Check if this is the closing statement
                if 'conn.close()' in current_line:
                    # Replace conn.close() with a comment
                    lines[j] = f"{' ' * current_indent}# Connection auto-closes here\n"
                    found_close = True
                    fixed_lines.append(f"{i+1} (open) and {j+1} (close)")
                    fixes_applied += 1
                    break

                # Increase indentation for lines that belong to this block
                # Only indent if the line is at the same level or deeper
                if current_indent >= indent and current_line.strip():
                    # Add 4 spaces of indentation
                    lines[j] = '    ' + current_line

                j += 1

            if not found_close:
                print(f"WARNING: Found open at line {i+1} but no matching close()")

            i = j  # Skip to after the block we just fixed

        i += 1

    # Write the fixed content back
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)

    return fixes_applied, fixed_lines


def main():
    """Run the connection leak fixer."""
    api_server_path = Path(__file__).parent / "api_server.py"

    print("="*60)
    print("DATABASE CONNECTION LEAK FIXER")
    print("="*60)
    print(f"Target file: {api_server_path}")
    print()

    # Create backup
    backup_path = api_server_path.with_suffix('.py.backup')
    import shutil
    shutil.copy2(api_server_path, backup_path)
    print(f"[OK] Backup created: {backup_path}")
    print()

    # Apply fixes
    print("Scanning for connection leaks...")
    fixes_applied, fixed_lines = fix_connection_leaks(str(api_server_path))

    print()
    print("="*60)
    print("RESULTS")
    print("="*60)
    print(f"Fixes applied: {fixes_applied}")

    if fixed_lines:
        print("\nFixed at lines:")
        for line_info in fixed_lines:
            print(f"  - {line_info}")

    print()
    print("="*60)
    print("NEXT STEPS")
    print("="*60)
    print("1. Review the changes in api_server.py")
    print("2. Test the API server: python api_server.py")
    print("3. Run syntax check: python -m py_compile api_server.py")
    print("4. If issues occur, restore from backup:")
    print(f"   copy {backup_path} {api_server_path}")
    print("="*60)


if __name__ == "__main__":
    main()
