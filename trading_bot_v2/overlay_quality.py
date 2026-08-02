"""Quality rules for stored regime parameter overlays.

An Optuna study always reports a "best trial", even when every candidate
lost money - the best trial is then merely the least-bad loser. On
2026-07-20 exactly that happened: a mean_reversion/ranging_calm overlay
measured at -1.486 Sharpe over 246 trades was stored ``active=1``, so
turning on ENABLE_REGIME_PARAM_OVERLAYS would have installed a
known-losing parameter set on a live strategy.

Two independent guards use the rules in this module:

1. WRITE side - :meth:`trading_bot_v2.database.DatabaseManager.
   save_regime_param_overlay` refuses to store a losing overlay.
2. APPLY side - :class:`trading_bot_v2.regime_param_overlay.
   RegimeParamOverlayManager` refuses to hand a losing overlay to a
   running strategy.

The apply-side guard is not redundant. The write guard only sees rows
that go through the save API; it cannot see a database restored from
``backups/*.db`` (:meth:`DatabaseManager.restore_backup` is a plain file
copy), a hand-edited or externally supplied database file, or a row
written before the guard existed.

WHY THIS MODULE EXISTS (import-cycle note):
    The break-even table has to be reachable from ``database.py`` and
    from ``regime_param_overlay.py``. ``database.py`` must not import
    the optimization package (``optimization/__init__`` pulls in the
    backtesting engine, which imports ``regime_param_overlay``), so
    ``optuna_runner.OBJECTIVE_ALIASES`` cannot be the source. This
    module imports nothing from the package, so both sides can depend on
    it without any cycle, and the rules exist exactly once.
"""

import os
from typing import Dict, Optional


class LosingOverlayRefused(ValueError):
    """Raised when a losing parameter overlay is stored without opt-in.

    See :meth:`trading_bot_v2.database.DatabaseManager.
    save_regime_param_overlay`. Carries the offending objective value so
    callers can report it verbatim.

    Re-exported from ``trading_bot_v2.database`` for backwards
    compatibility with existing callers and tests.
    """

    def __init__(
        self,
        message: str,
        strategy: str = "",
        regime: str = "",
        objective: Optional[str] = None,
        objective_value: Optional[float] = None,
        break_even: Optional[float] = None,
    ):
        super().__init__(message)
        self.strategy = strategy
        self.regime = regime
        self.objective = objective
        self.objective_value = objective_value
        self.break_even = break_even


#: Break-even value for every optimizer objective. All supported
#: objectives are higher-is-better, so a best-trial score at or below
#: this means the winning configuration did not make money.
OVERLAY_BREAK_EVEN_BY_OBJECTIVE: Dict[str, float] = {
    "sharpe_ratio": 0.0,
    "sortino_ratio": 0.0,
    "calmar_ratio": 0.0,
    "total_return_pct": 0.0,
    "profit_factor": 1.0,
}

#: Short forms accepted for the objective name. Mirrors
#: ``optimization.optuna_runner.OBJECTIVE_ALIASES`` without importing it
#: (see the module docstring for the cycle this avoids);
#: tests/test_regime_optimization.py asserts the two agree on every name
#: they share, so the mirror cannot drift silently.
OVERLAY_OBJECTIVE_ALIASES: Dict[str, str] = {
    "sharpe": "sharpe_ratio",
    "sortino": "sortino_ratio",
    "calmar": "calmar_ratio",
    "total_return": "total_return_pct",
    "pf": "profit_factor",
}

#: Fallback break-even for an unnamed or unrecognised objective. Every
#: metric this codebase optimizes is higher-is-better, so a negative
#: score is losing under all of them. Using 0.0 is the permissive
#: choice (profit_factor's real break-even is 1.0) but it closes the
#: hole where objective=None would otherwise skip the check entirely.
UNKNOWN_OBJECTIVE_BREAK_EVEN = 0.0

#: Environment opt-in shared by both guards. Setting it means "I know
#: this overlay was measured to lose (or was never measured) and I want
#: it stored and applied anyway".
OVERLAY_OPT_IN_ENV = "ALLOW_LOSING_REGIME_OVERLAYS"


def overlay_env_opt_in() -> bool:
    """True when ALLOW_LOSING_REGIME_OVERLAYS opts into losing overlays."""
    value = os.getenv(OVERLAY_OPT_IN_ENV, "").strip().lower()
    return value in ("true", "1", "yes", "on")


def overlay_break_even(objective: Optional[str]) -> float:
    """Return the break-even score for an optimizer objective.

    Args:
        objective: Objective name in short or canonical form. None or an
            unrecognised name falls back to
            :data:`UNKNOWN_OBJECTIVE_BREAK_EVEN`.

    Returns:
        The value at or below which a best trial is a losing result.
    """
    if objective is None:
        return UNKNOWN_OBJECTIVE_BREAK_EVEN
    key = str(objective).strip().lower()
    key = OVERLAY_OBJECTIVE_ALIASES.get(key, key)
    return OVERLAY_BREAK_EVEN_BY_OBJECTIVE.get(key, UNKNOWN_OBJECTIVE_BREAK_EVEN)


def overlay_rejection_reason(
    objective: Optional[str],
    objective_value: Optional[float],
    require_score: bool = True,
) -> Optional[str]:
    """Explain why a stored overlay must not be applied, if it must not.

    Args:
        objective: Objective name recorded with the overlay (may be None
            or unrecognised, in which case
            :data:`UNKNOWN_OBJECTIVE_BREAK_EVEN` is used - the same
            treatment the write guard gives it).
        objective_value: Best objective value recorded with the overlay.
        require_score: Treat a missing or unparseable value as a
            rejection. True on the apply side (an overlay about to touch
            live capital must carry a measured score); False on the
            write side, which cannot judge a row it was handed without
            one and only stores it as audit material.

    Returns:
        A short human-readable reason, or None when the overlay is
        acceptable.
    """
    label = objective or "unspecified objective"

    if objective_value is None:
        if not require_score:
            return None
        return (
            f"no recorded objective value ({label}) - an overlay with no "
            f"measured score is of unknown provenance and is not trusted "
            f"with live parameters"
        )

    try:
        value = float(objective_value)
    except (TypeError, ValueError):
        if not require_score:
            return None
        return (
            f"objective value {objective_value!r} ({label}) is not a "
            f"number, so the overlay cannot be shown to have won"
        )

    break_even = overlay_break_even(objective)
    if value <= break_even:
        return (
            f"recorded {label} = {value!r} is at or below break-even "
            f"{break_even}, so this parameter set was measured to lose"
        )
    return None
