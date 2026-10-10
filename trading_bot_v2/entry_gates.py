"""Flag-gated Kelly size cap and signal-pipeline gate for standard entries.

Both features default OFF and neither applies to grid entries:

- ``ENABLE_KELLY_SIZING``: the Kelly/confidence size is computed for a
  standard entry and the request becomes ``min(kelly_size, risk_manager
  size)``.  Kelly can therefore only reduce a position.  Whenever Kelly has
  no usable answer (too little trade history, a non-positive Kelly
  fraction, a missing stop, a non-finite result, or any exception) the
  RiskManager size is used unchanged and the reason is logged once.
- ``ENABLE_SIGNAL_PIPELINE``: the permission (Phase 1) and execution
  (Phase 3) checks of ``signal_phases`` run on the already generated
  signal.  They can only reject; they never alter the signal or its size.
  Phase 1's regime-to-strategy map is NOT enforced here because it
  contradicts StrategyManager's live activation rules.

With a flag off the corresponding object is never built, and
``confidence_sizer`` / ``signal_phases`` are not imported by this module.
"""

import math
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from .models import Signal

if TYPE_CHECKING:
    from .confidence_sizer import IntegratedPositionSizer
    from .signal_phases import SignalPipeline

SIZING_MODE_RISK_MANAGER = "risk_manager"
SIZING_MODE_KELLY_CAPPED = "kelly_capped"


def kelly_sizer_from_config(
    cfg: Any, db: Any, trade_store: Any
) -> Optional["IntegratedPositionSizer"]:
    """Build the Kelly/confidence sizer when ENABLE_KELLY_SIZING is true.

    Args:
        cfg: Config object carrying ``enable_kelly_sizing``.
        db: DatabaseManager handed to KellyPositionSizer.
        trade_store: TradeStore the Kelly sizer reads closed trades from.

    Returns:
        The sizer, or None when the flag is off or construction failed (in
        which case sizing stays on the RiskManager path).
    """
    if getattr(cfg, "enable_kelly_sizing", False) is not True:
        return None
    try:
        from .confidence_sizer import ConfidenceSizer, IntegratedPositionSizer
        from .kelly_position_sizer import KellyPositionSizer

        kelly = KellyPositionSizer(db=db, trade_store=trade_store)
        sizer = IntegratedPositionSizer(kelly, ConfidenceSizer())
    except Exception as exc:  # noqa: BLE001 - sizing must fall back, loudly
        logger.opt(exception=True).error(
            f"ENABLE_KELLY_SIZING is set but the Kelly sizer could not be "
            f"built ({type(exc).__name__}: {exc}); using RiskManager sizing"
        )
        return None
    logger.warning(
        "ENABLE_KELLY_SIZING=true: standard entries are sized "
        "min(Kelly/confidence, RiskManager)"
    )
    return sizer


def signal_pipeline_from_config(
    cfg: Any, risk_manager: Any, client: Any
) -> Optional["SignalPipeline"]:
    """Build the entry-review pipeline when ENABLE_SIGNAL_PIPELINE is true.

    A construction failure is NOT swallowed: an operator who asked for the
    extra gate must not get a bot that silently runs without it.

    Args:
        cfg: Config object carrying ``enable_signal_pipeline``.
        risk_manager: RiskManager the phases read exposure from.
        client: Raw exchange client (unused while the bot passes a balance).

    Returns:
        The pipeline, or None when the flag is off.
    """
    if getattr(cfg, "enable_signal_pipeline", False) is not True:
        return None
    from .signal_phases import (
        Phase1RegimePermission,
        Phase3ExecutionFilter,
        SignalPipeline,
    )

    phase1 = Phase1RegimePermission(
        regime_detector=None,
        risk_manager=risk_manager,
        config=cfg,
        enforce_regime=False,
    )
    phase3 = Phase3ExecutionFilter(risk_manager, client, cfg)
    logger.warning(
        "ENABLE_SIGNAL_PIPELINE=true: Phase 1 (without its regime map) and "
        "Phase 3 can reject standard entries"
    )
    return SignalPipeline(phase1, {}, phase3, position_sizer=None)


def sizing_mode(bot: Any) -> str:
    """Return the active standard-entry sizing mode for status payloads."""
    if getattr(bot, "_kelly_sizer", None) is not None:
        return SIZING_MODE_KELLY_CAPPED
    return SIZING_MODE_RISK_MANAGER


def kelly_capped_quantity(
    sizer: "IntegratedPositionSizer",
    signal: Signal,
    baseline_quantity: float,
    account_balance: float,
) -> float:
    """Return ``min(kelly_size, baseline_quantity)``, or the baseline.

    Args:
        sizer: Kelly/confidence sizer built by kelly_sizer_from_config.
        signal: Standard (non-grid) entry signal.
        baseline_quantity: Quantity RiskManager.get_position_size produced.
        account_balance: Current account balance.

    Returns:
        A quantity never greater than ``baseline_quantity``.  The baseline
        itself whenever Kelly has no usable answer.
    """
    tag = f"Kelly sizing {signal.asset} {signal.strategy.value}"
    if not baseline_quantity > 0:
        logger.info(f"{tag}: baseline size is {baseline_quantity}, nothing to cap")
        return baseline_quantity
    try:
        result = sizer.calculate_kelly_bounded_size(signal, account_balance)
    except Exception as exc:  # noqa: BLE001 - any sizer error keeps today's size
        logger.opt(exception=True).error(
            f"{tag}: sizer raised {type(exc).__name__}: {exc}; "
            f"using RiskManager size {baseline_quantity:.6f}"
        )
        return baseline_quantity

    kelly_quantity = result.effective_size
    if result.kelly_status != "ok":
        why = f"{result.kelly_status} ({result.reason})"
    elif not math.isfinite(kelly_quantity) or kelly_quantity <= 0:
        why = f"unusable Kelly size {kelly_quantity}"
    else:
        final = min(kelly_quantity, baseline_quantity)
        logger.info(
            f"{tag}: kelly={kelly_quantity:.6f}, risk_manager="
            f"{baseline_quantity:.6f} -> {final:.6f}"
        )
        return final
    logger.info(f"{tag}: {why}; using RiskManager size {baseline_quantity:.6f}")
    return baseline_quantity


def pipeline_block_reason(
    pipeline: "SignalPipeline",
    signal: Signal,
    quantity: float,
    account_balance: float,
) -> Optional[str]:
    """Return why the signal pipeline rejects this entry, or None to proceed.

    Args:
        pipeline: Pipeline built by signal_pipeline_from_config.
        signal: Standard (non-grid) entry signal; not modified.
        quantity: Quantity about to be sent.
        account_balance: Current account balance.

    Returns:
        A rejection reason, or None when every phase passed.
    """
    decision = pipeline.review_entry(signal, quantity, account_balance)
    # Anything other than an explicit PASS rejects (fail closed).
    if decision.result.value != "pass":
        return f"Signal pipeline: {decision.reason}"
    return None
