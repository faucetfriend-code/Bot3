"""Inert image acceptance check; never import or launch the bot application."""

import importlib
import os
from pathlib import Path


def validate_layout(root: Path) -> int:
    """Check packaged source and dashboard without executing application code."""
    dashboard = root / "interface.html"
    if not dashboard.is_file() or dashboard.stat().st_size == 0:
        raise RuntimeError("Missing dashboard artifact")
    package = root / "trading_bot_v2"
    if not (package / "api_server.py").is_file():
        raise RuntimeError("Missing module entrypoint")
    if list(root.rglob(".env")) or list(root.rglob(".env.*")):
        raise RuntimeError("Environment file unexpectedly packaged in image")
    count = 0
    for path in package.rglob("*.py"):
        compile(path.read_bytes(), str(path), "exec")
        count += 1
    return count


def main() -> None:
    """Validate packaging and native dependencies with container networking off."""
    if hasattr(os, "getuid") and os.getuid() == 0:
        raise RuntimeError("Image must run as the non-root application user")
    count = validate_layout(Path("/app"))
    modules = (
        "fastapi",
        "uvicorn",
        "pydantic",
        "requests",
        "solders",
        "numpy",
        "pandas",
        "pyarrow",
        "sklearn",
        "hmmlearn",
        "psycopg2",
        "aiosqlite",
        "websockets",
        "prometheus_client",
        "joblib",
        "optuna",
    )
    for module in modules:
        importlib.import_module(module)
    print(f"Image smoke passed: {count} source files; dependency imports available")


if __name__ == "__main__":
    main()
