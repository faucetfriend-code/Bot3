#!/usr/bin/env python3
"""
Trading Bot Task Runner
Cross-platform task runner for build, test, and development commands
"""

import subprocess
import sys
import os
from pathlib import Path
from typing import List, Optional


class TaskRunner:
    def __init__(self):
        self.project_root = Path(__file__).parent
        self.is_windows = os.name == "nt"

    def run_command(self, cmd: List[str], cwd: Optional[Path] = None) -> int:
        """Run a command and return the exit code"""
        try:
            # Ensure Python scripts are in PATH on Windows
            env = os.environ.copy()
            if self.is_windows:
                python_scripts = Path(sys.executable).parent / "Scripts"
                if python_scripts.exists():
                    env["PATH"] = (
                        str(python_scripts) + os.pathsep + env.get("PATH", "")
                    )

            result = subprocess.run(
                cmd,
                cwd=cwd or self.project_root,
                shell=self.is_windows,
                env=env,
            )
            return result.returncode
        except KeyboardInterrupt:
            print("\nTask interrupted by user")
            return 1
        except Exception as e:
            print(f"Error running command: {e}")
            return 1

    def install(self):
        """Install Python and Node.js dependencies"""
        print("Installing dependencies...")

        # Python dependencies
        if (
            self.run_command(
                [
                    sys.executable,
                    "-m",
                    "poetry",
                    "install",
                ]
            )
            != 0
        ):
            return False

        # Node.js dependencies
        if self.run_command(["npm", "install"]) != 0:
            return False

        print("Dependencies installed successfully")
        return True

    def test(self, file: Optional[str] = None, pattern: Optional[str] = None):
        """Run tests"""
        cmd = [sys.executable, "-m", "poetry", "run", "pytest"]
        if file:
            cmd.append(file)
        elif pattern:
            cmd.extend(["-k", pattern])
        else:
            cmd.append("tests/")

        return self.run_command(cmd) == 0

    def lint(self):
        """Run linting tools"""
        print("Running linters...")

        success = True

        # Python linting
        if self.run_command([sys.executable, "-m", "poetry", "run", "flake8"]) != 0:
            success = False

        if self.run_command([sys.executable, "-m", "poetry", "run", "mypy", "."]) != 0:
            success = False

        if (
            self.run_command([sys.executable, "-m", "poetry", "run", "black", "--check", "."])
            != 0
        ):
            success = False

        # JavaScript linting
        if self.run_command(["npm", "run", "check"]) != 0:
            success = False

        if success:
            print("All linting checks passed")
        return success

    def lint_fix(self):
        """Auto-fix linting issues"""
        print("Auto-fixing linting issues...")

        success = True

        # Python auto-fix
        if self.run_command([sys.executable, "-m", "poetry", "run", "black", "."]) != 0:
            success = False

        if success:
            print("Auto-fixing completed")
        return success

    def run_bot(self):
        """Run the main trading bot"""
        return self.run_command([sys.executable, "main.py"]) == 0

    def run_api(self):
        """Run the API server"""
        return (
            self.run_command(
                [
                    sys.executable,
                    "-m",
                    "poetry",
                    "run",
                    "uvicorn",
                    "api_server:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                ]
            )
            == 0
        )

    def run_analyze(self):
        """Run analysis"""
        return self.run_command(["npm", "run", "analyze"]) == 0

    def validate(self):
        """Run all validation agents"""
        if self.is_windows:
            script = "scripts\\validate_all.bat"
            return self.run_command([script]) == 0
        else:
            script = "./scripts/validate_all.sh"
            return self.run_command([script]) == 0

    def agent_status(self):
        """Check agent system status"""
        print("Agent System Status")
        print("=" * 20)

        # Check Python version
        print("Python Environment:")
        result = self.run_command([sys.executable, "--version"])
        print()

        # Check required packages
        print("Required Packages:")
        packages = ["langchain", "langchain-anthropic", "anthropic"]
        all_installed = True

        for pkg in packages:
            try:
                # Try to import the package
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        f"import {pkg}; print({pkg}.__version__)",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=self.project_root,
                )
                if result.returncode == 0:
                    version = result.stdout.strip() or "unknown"
                    print(f"  [OK] {pkg} ({version})")
                else:
                    print(f"  [MISSING] {pkg} (not installed)")
                    all_installed = False
            except Exception:
                print(f"  [ERROR] {pkg} (error checking)")
                all_installed = False
        print()

        # Check .env file
        print("Configuration:")
        env_file = self.project_root / ".env"
        if env_file.exists():
            print("  [OK] .env file exists")
        else:
            print("  [MISSING] .env file missing")
        print()

        if all_installed and env_file.exists():
            print("Agent system ready!")
        else:
            print("Agent system needs configuration")
            if not all_installed:
                print("Install dependencies: python tasks.py install")
            if not env_file.exists():
                print("Create .env file: cp .env.example .env")

        return all_installed

    def generate_tests(self):
        """Generate tests for uncovered modules"""
        if self.is_windows:
            print("Test generation not available on Windows")
            print(
                "Run on Linux/macOS or use: python agents/test_generator.py --module MODULE --output tests/test_MODULE.py"
            )
            return False
        else:
            return self.run_command(["./scripts/generate_tests.sh"]) == 0

    def clean(self):
        """Clean up generated files and caches"""
        print("Cleaning up...")

        # Python cache files
        for pattern in [
            "__pycache__",
            "*.pyc",
            "*.pyo",
            "*.egg-info",
            ".pytest_cache",
            ".mypy_cache",
        ]:
            for path in self.project_root.rglob(pattern):
                if path.is_dir():
                    import shutil

                    shutil.rmtree(path)
                else:
                    path.unlink()

        # Reports directory
        reports_dir = self.project_root / "reports"
        if reports_dir.exists():
            import shutil

            shutil.rmtree(reports_dir)

        # Node.js clean
        self.run_command(["npm", "run", "clean"])

        print("Cleanup completed")
        return True

    def help(self):
        """Show available commands"""
        commands = {
            "install": "Install Python and Node.js dependencies",
            "test": "Run all tests",
            "test --file FILE": "Run a single test file",
            "test --pattern PATTERN": "Run tests by name pattern",
            "lint": "Run linting tools",
            "lint-fix": "Auto-fix linting issues",
            "run-bot": "Run the main trading bot",
            "run-api": "Run the API server",
            "run-analyze": "Run analysis",
            "validate": "Run all validation agents",
            "agent-status": "Check agent system status",
            "generate-tests": "Generate tests for uncovered modules",
            "clean": "Clean up generated files and caches",
            "help": "Show this help message",
        }

        print("Trading Bot Task Runner")
        print("=" * 40)
        print("Available commands:")
        for cmd, desc in commands.items():
            print(f"  {cmd:<25} {desc}")


def main():
    runner = TaskRunner()

    if len(sys.argv) < 2:
        runner.help()
        return

    command = sys.argv[1]

    if command == "install":
        success = runner.install()
    elif command == "test":
        file_arg = None
        pattern_arg = None
        if len(sys.argv) > 2:
            if sys.argv[2] == "--file" and len(sys.argv) > 3:
                file_arg = sys.argv[3]
            elif sys.argv[2] == "--pattern" and len(sys.argv) > 3:
                pattern_arg = sys.argv[3]
        success = runner.test(file=file_arg, pattern=pattern_arg)
    elif command == "lint":
        success = runner.lint()
    elif command == "lint-fix":
        success = runner.lint_fix()
    elif command == "run-bot":
        success = runner.run_bot()
    elif command == "run-api":
        success = runner.run_api()
    elif command == "run-analyze":
        success = runner.run_analyze()
    elif command == "validate":
        success = runner.validate()
    elif command == "agent-status":
        success = runner.agent_status()
    elif command == "generate-tests":
        success = runner.generate_tests()
    elif command == "clean":
        success = runner.clean()
    elif command == "help":
        runner.help()
        return
    else:
        print(f"Unknown command: {command}")
        print("Run 'python tasks.py help' for available commands")
        return

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
