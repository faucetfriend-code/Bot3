"""Startup validation of the .env file and the API server binding.

Two classes of problem are caught here before uvicorn starts:

* Keys assigned more than once in ``.env``. python-dotenv keeps the LAST
  assignment, so a later empty duplicate silently kills a real credential
  (this is how the live Blofin credentials were shadowed while the bot
  ran on demo - see CLAUDE.md).
* An exchange configured for LIVE trading whose credentials resolve
  empty, and a non-loopback API bind without an API token.

VALUES ARE NEVER LOGGED, RETURNED OR RAISED. Every report and every error
message names keys only. Lengths are the most this module will disclose.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from dotenv import dotenv_values

_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_TRUE_VALUES = ("true", "1", "yes")

#: Host names accepted as loopback without parsing.
LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})

#: Credentials that must be non-empty per exchange when it is LIVE.
LIVE_CREDENTIALS: Dict[str, Tuple[str, ...]] = {
    "blofin": ("BLOFIN_API_KEY", "BLOFIN_API_SECRET", "BLOFIN_PASSPHRASE"),
    "pacifica": ("AGENT_WALLET_PRIVATE_KEY", "ACCOUNT_PUBLIC_KEY"),
}

#: Default .env location: the repository root, one level above the package.
DEFAULT_DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class StartupConfigError(RuntimeError):
    """Raised when the configuration is unsafe to start the server with."""


@dataclass
class EnvValidationReport:
    """Result of validating one .env file. Holds key names, never values.

    Attributes:
        path: The file that was inspected, or None if it did not exist.
        duplicate_keys: Keys assigned more than once, sorted.
        errors: Fatal problems; a non-empty list must refuse startup.
    """

    path: Optional[str]
    duplicate_keys: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def _is_true(raw: Optional[str], default: bool) -> bool:
    """Interpret a boolean env value the way config.py does.

    Args:
        raw: The raw string, or None when the key is absent.
        default: Value to use when the key is absent or blank.

    Returns:
        True for "true", "1" or "yes" (case-insensitive), else False.
    """
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUE_VALUES


def is_loopback_host(host: Optional[str]) -> bool:
    """Return True when the bind host can only be reached from this machine.

    Args:
        host: A host name or IP literal as it would be passed to uvicorn.

    Returns:
        True for localhost, 127.0.0.0/8 and ::1; False for anything else,
        including 0.0.0.0, an empty string and unparsable input.
    """
    if not host:
        return False
    candidate = host.strip().lower()
    if candidate in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def check_api_binding(host: Optional[str], token: Optional[str]) -> Optional[str]:
    """Check that a bind host / token pair is safe to serve on.

    Args:
        host: The configured API_HOST.
        token: The configured API_TOKEN, or None when unset.

    Returns:
        None when safe, otherwise a human-readable refusal reason.
    """
    if token or is_loopback_host(host):
        return None
    return (
        f"API_HOST={host!r} is reachable from other machines but API_TOKEN is "
        "unset. The control interface can place real orders, so either set "
        "API_TOKEN in .env or bind to 127.0.0.1."
    )


def find_duplicate_env_keys(text: str) -> List[str]:
    """Find keys assigned more than once in raw dotenv text.

    Args:
        text: The full contents of a .env file.

    Returns:
        Sorted key names that appear on two or more assignment lines.
        Comment and blank lines are ignored. Values are never inspected.
    """
    counts: Dict[str, int] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT_RE.match(line)
        if match:
            key = match.group(1)
            counts[key] = counts.get(key, 0) + 1
    return sorted(key for key, count in counts.items() if count > 1)


def check_live_credentials(values: Mapping[str, Optional[str]]) -> List[str]:
    """Refuse a LIVE exchange configuration whose credentials are empty.

    Args:
        values: Resolved key -> value mapping, i.e. the last assignment of
            each key wins exactly as python-dotenv resolves it.

    Returns:
        Error messages naming the missing keys. Empty when the exchange is
        on demo/testnet or every required credential is non-empty.
    """
    exchange = (values.get("EXCHANGE") or "pacifica").strip().lower() or "pacifica"
    if exchange == "blofin":
        live = not _is_true(values.get("BLOFIN_DEMO"), True)
        mode_key = "BLOFIN_DEMO"
    elif exchange == "pacifica":
        live = not _is_true(values.get("TESTNET"), True)
        mode_key = "TESTNET"
    else:
        return []
    if not live:
        return []

    required: Sequence[str] = LIVE_CREDENTIALS[exchange]
    missing = [key for key in required if not (values.get(key) or "").strip()]
    if not missing:
        return []
    return [
        f"EXCHANGE={exchange} with {mode_key}=false is LIVE trading, but these "
        f"credentials resolve empty: {', '.join(missing)}. A later duplicate "
        "assignment in .env overrides an earlier real value - check for one."
    ]


def validate_env_file(path: Optional[str] = None) -> EnvValidationReport:
    """Validate a .env file: duplicate keys and live-credential completeness.

    Args:
        path: File to inspect. None uses the repository-root .env.

    Returns:
        An EnvValidationReport. A missing file yields an empty report with
        ``path`` None; nothing is loaded into the process environment.
    """
    target = Path(path) if path else DEFAULT_DOTENV_PATH
    if not target.is_file():
        return EnvValidationReport(path=None)

    text = target.read_text(encoding="utf-8", errors="replace")
    resolved = dotenv_values(str(target))
    return EnvValidationReport(
        path=str(target),
        duplicate_keys=find_duplicate_env_keys(text),
        errors=check_live_credentials(resolved),
    )


def run_startup_validation(
    dotenv_path: Optional[str] = None,
    host: Optional[str] = None,
    token: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> EnvValidationReport:
    """Run every startup check, warn on duplicates and raise on fatal issues.

    Args:
        dotenv_path: .env file to inspect; None for the repository default.
        host: Configured API_HOST. None skips the binding check.
        token: Configured API_TOKEN (its value is only compared, never logged).
        log: Logger for warnings; defaults to this module's logger.

    Returns:
        The EnvValidationReport when startup may proceed.

    Raises:
        StartupConfigError: When live credentials resolve empty or the API
            bind is non-loopback without a token. The message names keys only.
    """
    logger = log or logging.getLogger(__name__)
    report = validate_env_file(dotenv_path)

    if report.duplicate_keys:
        logger.warning(
            "%s assigns %d key(s) more than once; python-dotenv keeps the LAST "
            "assignment, so an earlier real value is silently overridden: %s",
            report.path,
            len(report.duplicate_keys),
            ", ".join(report.duplicate_keys),
        )

    if host is not None:
        binding_error = check_api_binding(host, token)
        if binding_error:
            report.errors.append(binding_error)

    if report.errors:
        raise StartupConfigError(
            "Refusing to start:\n  - " + "\n  - ".join(report.errors)
        )
    return report
