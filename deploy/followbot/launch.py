"""Paper-only Linux follower preflight; run only on the provisioned VPS."""

import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


class PreflightError(RuntimeError):
    """A missing or unsafe prerequisite that requires operator attention."""


def validate_settings(environ: dict[str, str], source: Path) -> None:
    """Validate isolated Pacifica paper-mode configuration without importing bot code."""
    required = {
        "EXCHANGE": "pacifica",
        "PAPER_MODE": "true",
        "PACIFICA_TESTNET": "true",
        "FOLLOWBOT_SESSION_CONFIRMED": "true",
    }
    for name, expected in required.items():
        if environ.get(name, "").strip().lower() != expected:
            raise PreflightError(f"{name} must explicitly be {expected}")
    if not (source / "bot.py").is_file():
        raise PreflightError("Configured source directory has no bot.py")
    if any((directory / ".env").exists() for directory in (source, *source.parents)):
        raise PreflightError(
            "Remove source/ancestor .env files; use the external service environment"
        )
    for name in ("LOCAL_LLM_BASE_URL", "LOCAL_LLM_MODEL", "LOCAL_VISION_MODEL"):
        if not environ.get(name, "").strip():
            raise PreflightError(f"Missing {name}")
    if environ.get("DASHBOARD_HOST", "127.0.0.1") != "127.0.0.1":
        raise PreflightError("DASHBOARD_HOST must remain 127.0.0.1")


def read_json(url: str) -> object:
    """Read a small preflight response without logging URLs or response contents."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read(1048577)
    if len(body) > 1048576:
        raise PreflightError("Preflight response exceeded size limit")
    return json.loads(body)


def verify_services(environ: dict[str, str]) -> None:
    """Verify the private browser and configured models; never call an exchange."""
    targets = read_json("http://127.0.0.1:9222/json")
    if not isinstance(targets, list) or not any(
        isinstance(target, dict)
        and str(target.get("url", "")).startswith("https://discord.com/channels/")
        for target in targets
    ):
        raise PreflightError(
            "Browser has no Discord channel page; complete private session setup"
        )
    models = read_json(environ["LOCAL_LLM_BASE_URL"].rstrip("/") + "/models")
    if not isinstance(models, dict) or not isinstance(models.get("data"), list):
        raise PreflightError(
            "Model endpoint did not return an OpenAI-compatible model list"
        )
    available = {model.get("id") for model in models["data"] if isinstance(model, dict)}
    if not {environ["LOCAL_LLM_MODEL"], environ["LOCAL_VISION_MODEL"]} <= available:
        raise PreflightError("Configured text/vision model is unavailable")


def main() -> int:
    """Check prerequisites before replacing this process with the follower."""
    source = Path(
        os.environ.get("FOLLOWBOT_SOURCE", "/var/lib/discord-follow/app")
    ).resolve()
    try:
        validate_settings(dict(os.environ), source)
        verify_services(dict(os.environ))
    except (PreflightError, OSError, ValueError, urllib.error.URLError) as exc:
        # Avoid exception details from HTTP libraries: URLs may contain secrets.
        message = str(exc) if isinstance(exc, PreflightError) else type(exc).__name__
        print(f"Follower preflight blocked: {message}", file=sys.stderr)
        return 78
    os.chdir(source)
    os.execv(sys.executable, [sys.executable, str(source / "bot.py")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
