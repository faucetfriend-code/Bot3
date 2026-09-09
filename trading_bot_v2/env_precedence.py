"""Which source wins for each configuration key, and what a flip changes.

``config.py`` has always called ``load_dotenv(override=True)``, so the
.env FILE beats anything already exported in the process environment.
That is backwards from the usual precedence (flag > process env > file >
code default) and it produces a two-tier system nobody can predict from
the outside: whether a shell export takes effect depends on the accident
of whether that key happens to appear in .env.

Measured instances, both in the composite tuning path:

- ``REGIME_VOL_STRATEGIES_*`` are NOT in .env, so
  ``monthly_retune`` can pass them through ``subprocess(env=...)`` and
  they work.
- ``DIRECTIONAL_GATE`` and ``LOG_LEVEL`` ARE in .env, so the same
  technique is silently clobbered, and ``run_composite_tuning`` carries
  dedicated ``--directional-gate`` / ``--log-level`` flags that poke
  ``os.environ`` after import purely to work around it.

Same mechanism, opposite outcome, no way to tell which case you are in
without reading .env.

STAGED FIX, step 1 (this module): make the precedence explicit and
measurable WITHOUT changing it. ``load_env`` still defaults to
override=True, so behaviour is identical to before; what is new is that
it reports every key where the two sources disagree - the exact set
whose resolution would flip if override became False. Run

    python -m trading_bot_v2.env_precedence

to see that set for the current shell. An empty set means step 2 (the
actual flip) is a no-op for that environment and can be taken safely.

Step 2 is then either ``DOTENV_OVERRIDE=false`` in the process
environment or changing the default here.

VALUES ARE NEVER EXPOSED. .env holds AGENT_WALLET_PRIVATE_KEY and
ACCOUNT_PUBLIC_KEY; this module compares values in memory and reports
only KEY NAMES, so its output is safe to log, paste and check in.
"""

import logging
import os
from typing import Any, Dict, List, Mapping, Optional

from dotenv import dotenv_values, load_dotenv

logger = logging.getLogger(__name__)

#: Process-environment variable selecting the precedence. Read from the
#: real environment BEFORE .env is loaded, so putting it in .env cannot
#: work - by the time the file is read the decision has been made.
OVERRIDE_ENV_VAR = "DOTENV_OVERRIDE"

#: Shipped default. True reproduces the historical behaviour (.env wins
#: over the process environment). Step 2 of the staged fix flips this.
DEFAULT_OVERRIDE = True


def _truthy(raw: Optional[str], default: bool) -> bool:
    """Parse a config flag the way config.py parses its booleans.

    Args:
        raw: Raw string value, or None when unset.
        default: Value to use when unset or empty.

    Returns:
        The parsed boolean.
    """
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def shadowed_keys(
    dotenv_path: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> List[str]:
    """Keys set in BOTH sources with DIFFERENT values.

    These are the only keys whose resolved value depends on the override
    setting. A key present in just one source resolves the same way
    either way, and a key whose two values agree is invisible to the
    flip.

    Args:
        dotenv_path: Path to the .env file. None lets python-dotenv find
            it the same way ``load_dotenv`` does.
        environ: Process environment to compare against. Defaults to the
            live ``os.environ``.

    Returns:
        Sorted key NAMES. Never values - see the module docstring.
    """
    env = os.environ if environ is None else environ
    file_values = dotenv_values(dotenv_path) if dotenv_path else dotenv_values()
    return sorted(
        key
        for key, file_value in file_values.items()
        if key in env and file_value is not None and env[key] != file_value
    )


def load_env(
    dotenv_path: Optional[str] = None,
    override: Optional[bool] = None,
) -> Dict[str, Any]:
    """Load .env and report what the precedence choice actually decided.

    Call this exactly once, at config import. It replaces the bare
    ``load_dotenv(override=True)`` and is behaviour-identical while
    ``DEFAULT_OVERRIDE`` stays True.

    Args:
        dotenv_path: Path to the .env file, or None to autodetect.
        override: Force the precedence. None reads ``DOTENV_OVERRIDE``
            from the process environment, falling back to
            ``DEFAULT_OVERRIDE``.

    Returns:
        Report dict with ``override`` (the precedence used),
        ``override_source`` ("default", "environment" or "argument"),
        ``shadowed`` (key names whose resolution the setting decided),
        ``winner`` ("dotenv_file" or "process_environment") and
        ``dotenv_key_count``. No values.
    """
    if override is None:
        raw = os.environ.get(OVERRIDE_ENV_VAR)
        resolved = _truthy(raw, DEFAULT_OVERRIDE)
        source = "environment" if raw is not None else "default"
    else:
        resolved = override
        source = "argument"

    shadowed = shadowed_keys(dotenv_path)
    file_values = dotenv_values(dotenv_path) if dotenv_path else dotenv_values()

    if dotenv_path:
        load_dotenv(dotenv_path, override=resolved)
    else:
        load_dotenv(override=resolved)

    report: Dict[str, Any] = {
        "override": resolved,
        "override_source": source,
        "winner": "dotenv_file" if resolved else "process_environment",
        "shadowed": shadowed,
        "dotenv_key_count": len(file_values),
    }

    if shadowed:
        # Key names only. Never the values.
        logger.warning(
            "env precedence: %d key(s) set in both .env and the process "
            "environment with different values; .env %s. Flipping "
            "%s would change: %s",
            len(shadowed),
            "wins (override=True)" if resolved else "loses (override=False)",
            OVERRIDE_ENV_VAR,
            ", ".join(shadowed),
        )
    return report


def _print_report(report: Dict[str, Any]) -> None:
    """Print the precedence report for a human."""
    print("ENV PRECEDENCE")
    print(
        f"  override        : {report['override']} (from {report['override_source']})"
    )
    print(f"  winner          : {report['winner']}")
    print(f"  .env keys       : {report['dotenv_key_count']}")
    shadowed = report["shadowed"]
    print(f"  shadowed keys   : {len(shadowed)}")
    for key in shadowed:
        print(f"      {key}")
    print()
    if not shadowed:
        print("  No key is set in both sources with a different value, so")
        print("  flipping override changes NOTHING in this environment.")
        print("  Step 2 of the staged fix is safe to take here.")
    else:
        print("  Each key above resolves differently under override=False.")
        print("  Check each one before taking step 2. DATABASE_PATH in this")
        print("  list is the dangerous case: a stale shell export would")
        print("  start winning and point the bot at another database.")


def main() -> int:
    """Report the precedence for the current shell without changing it.

    Loads .env exactly as config.py would, then prints which keys the
    override setting decided. Values are never printed.

    Returns:
        0 when no key is shadowed, 1 when at least one is - so this can
        gate step 2 from a script.
    """
    report = load_env()
    _print_report(report)
    return 0 if not report["shadowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
