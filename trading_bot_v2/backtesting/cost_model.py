"""
Cost Model
==========

Transaction-cost estimates for the backtester: exchange fees (maker vs
taker), slippage, and the take-profit haircut applied to a signal before
it is routed to the simulated exchange.

Why the haircut lives here (not in the exchange): strategies should see
cost-adjusted expected values when calculating RRR, so low-RRR signals
are filtered out under realistic cost assumptions.

PROVENANCE OF THE NUMBERS
-------------------------
Established from the exchange's published schedule:

* Pacifica perp fees, base (tier-1) volume band: **0.015% maker /
  0.040% taker** (docs.pacifica.fi/trading-on-pacifica/trading-fees,
  read 2026-07-28; lower VIP tiers and a beta promo exist, so the base
  band is the conservative choice).

NOT established - these are estimates, flagged as such:

* Per-symbol half-spread. The repo contains no order-book snapshots and
  no recorded live fills (the ``trades`` table is empty; the live
  execution rate was ~1.2% because of Pacifica per-key IP whitelisting).
  The defaults below are ranked by liquidity and sit inside the 2-10 bp
  band this repo's own research notes quote for Solana perps
  (``research/high speed upgrades.txt``). Override per symbol via env
  once real fills exist.
* The impact and volatility coefficients of the slippage model.

The legacy constants (0.20% slippage, 0.06% taker, 0.02% maker) have no
recorded source anywhere in the repo. ``docs/tuning-log.md`` restates
them, but as configuration, not as evidence.

PROFILES
--------
``BACKTEST_COST_PROFILE`` selects the cost regime:

* ``legacy`` (default) - reproduces the historic behaviour bit for bit:
  one flat slippage number and one flat fee pair for every symbol.
  Deliberately the default so existing results stay comparable; it is
  also the *pessimistic* profile, which is the conservative direction.
* ``pacifica`` - published fee schedule, per-symbol overrides, and
  slippage modelled from spread + bar volatility + order participation.

Every knob is overridable per symbol, so ``pacifica`` is a starting
point rather than a claim of precision.
"""

import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from loguru import logger

from ..models import Signal

# ---------------------------------------------------------------------------
# Legacy constants (the historic hardcoded model)
# ---------------------------------------------------------------------------
LEGACY_SLIPPAGE_PCT = 0.002
LEGACY_TAKER_FEE_PCT = 0.0006
LEGACY_MAKER_FEE_PCT = 0.0002

# ---------------------------------------------------------------------------
# Published Pacifica base-tier perp fees (see PROVENANCE above)
# ---------------------------------------------------------------------------
PACIFICA_TAKER_FEE_PCT = 0.0004
PACIFICA_MAKER_FEE_PCT = 0.00015

# ---------------------------------------------------------------------------
# Slippage model defaults (ESTIMATES - see PROVENANCE above)
# ---------------------------------------------------------------------------
# Half-spread per symbol root, i.e. the cost of crossing the book once.
DEFAULT_HALF_SPREAD_PCT: Dict[str, float] = {
    "BTC": 0.00005,   # 0.5 bp
    "ETH": 0.00006,   # 0.6 bp
    "SOL": 0.00010,   # 1.0 bp
    "SUI": 0.00020,   # 2.0 bp
}
# Applied to any symbol not in the table above.
FALLBACK_HALF_SPREAD_PCT = 0.00025

# Fraction of the current bar's high-low range added as adverse fill drift.
DEFAULT_VOL_COEF = 0.05
# Square-root market-impact coefficient on order participation
# (participation = order notional / bar notional).
DEFAULT_IMPACT_COEF = 0.02
# Hard ceiling so a thin or malformed bar cannot produce absurd slippage.
DEFAULT_MAX_SLIPPAGE_PCT = 0.005

PROFILE_LEGACY = "legacy"
PROFILE_PACIFICA = "pacifica"
SUPPORTED_PROFILES = (PROFILE_LEGACY, PROFILE_PACIFICA)
DEFAULT_PROFILE = PROFILE_LEGACY

# The backtest engine turns a signal into a resting limit order when the
# requested entry is further than this fraction from the current price,
# and into a market order otherwise (see BacktestEngine._execute_signal).
# Mirrored here so the cost haircut charges the fee the fill will
# actually pay. test_cost_model.py pins the two together.
LIMIT_ENTRY_PRICE_GAP_PCT = 0.001


class LiquidityRole(str, Enum):
    """Which side of the book a fill takes."""

    MAKER = "maker"
    TAKER = "taker"


def symbol_root(symbol: str) -> str:
    """Return the base asset of a market symbol.

    Args:
        symbol: Market symbol such as "BTC-USDC" or "SUI".

    Returns:
        Upper-cased base asset ("BTC", "SUI"). Empty string for falsy
        input.
    """
    if not symbol:
        return ""
    return str(symbol).upper().split("-")[0].split("/")[0].split("_")[0]


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    """Read a float from the environment, warning and falling back on junk.

    Mirrors the warn-and-fall-back convention used elsewhere in the
    codebase (see strategies/vwap_scalping.py::validate_sd_entry_threshold):
    a bad cost knob should not take a run down, but it must not pass
    silently either.

    Args:
        name: Environment variable name.
        default: Value used when unset or invalid.
        minimum: Lowest accepted value, inclusive.
        maximum: Highest accepted value, inclusive.

    Returns:
        The parsed value if valid, else ``default``.
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            f"{name}={raw!r} is not a number - falling back to {default}"
        )
        return default
    if not (minimum <= value <= maximum):
        logger.warning(
            f"{name}={value} is outside [{minimum}, {maximum}] - "
            f"falling back to {default}"
        )
        return default
    return value


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean from the environment, warning and falling back on junk.

    Args:
        name: Environment variable name.
        default: Value used when unset or invalid.

    Returns:
        The parsed boolean if valid, else ``default``.
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    logger.warning(
        f"{name}={raw!r} is not a boolean - falling back to {default}"
    )
    return default


def resolve_cost_profile(value: Optional[str] = None) -> str:
    """Validate a cost-profile name, warning and falling back if unknown.

    Args:
        value: Profile name. ``None`` reads ``BACKTEST_COST_PROFILE``.

    Returns:
        A name in :data:`SUPPORTED_PROFILES`, else :data:`DEFAULT_PROFILE`.
    """
    raw = value if value is not None else os.getenv("BACKTEST_COST_PROFILE")
    if raw is None or raw == "":
        return DEFAULT_PROFILE
    candidate = str(raw).strip().lower()
    if candidate in SUPPORTED_PROFILES:
        return candidate
    logger.warning(
        f"BACKTEST_COST_PROFILE={raw!r} is not one of "
        f"{SUPPORTED_PROFILES} - falling back to {DEFAULT_PROFILE}"
    )
    return DEFAULT_PROFILE


@dataclass(frozen=True)
class SymbolCosts:
    """Resolved cost parameters for one symbol.

    Attributes:
        symbol: Symbol root the parameters were resolved for.
        maker_fee_pct: Fee fraction charged on a passive (limit) fill.
        taker_fee_pct: Fee fraction charged on an aggressive fill.
        half_spread_pct: Cost of crossing the book once.
        vol_coef: Fraction of the bar range added as adverse drift.
        impact_coef: Square-root impact coefficient on participation.
        max_slippage_pct: Ceiling on total modelled slippage.
        flat_slippage_pct: When set, slippage is this fixed fraction and
            the dynamic terms are ignored (legacy profile, or an explicit
            per-symbol override).
    """

    symbol: str
    maker_fee_pct: float
    taker_fee_pct: float
    half_spread_pct: float
    vol_coef: float
    impact_coef: float
    max_slippage_pct: float
    flat_slippage_pct: Optional[float] = None

    def fee_pct(self, role: LiquidityRole) -> float:
        """Return the fee fraction for a fill in ``role``."""
        return (
            self.maker_fee_pct
            if role == LiquidityRole.MAKER
            else self.taker_fee_pct
        )


class CostTable:
    """Per-symbol, per-role cost lookup.

    The table is the single place that answers "what does this fill
    cost?". Both :class:`CostModel` (pre-trade haircut) and
    ``SimulatedExchange`` (actual fills) resolve through it, so the two
    can never drift apart.
    """

    def __init__(
        self,
        profile: str = DEFAULT_PROFILE,
        base_maker_fee_pct: float = LEGACY_MAKER_FEE_PCT,
        base_taker_fee_pct: float = LEGACY_TAKER_FEE_PCT,
        base_slippage_pct: float = LEGACY_SLIPPAGE_PCT,
        vol_coef: float = DEFAULT_VOL_COEF,
        impact_coef: float = DEFAULT_IMPACT_COEF,
        max_slippage_pct: float = DEFAULT_MAX_SLIPPAGE_PCT,
        read_env_overrides: bool = True,
    ) -> None:
        """Build a cost table.

        Args:
            profile: ``legacy`` or ``pacifica``; unknown names fall back.
            base_maker_fee_pct: Global maker fee before per-symbol
                overrides.
            base_taker_fee_pct: Global taker fee before per-symbol
                overrides.
            base_slippage_pct: Flat slippage used by the ``legacy``
                profile.
            vol_coef: Fraction of the bar range added as adverse drift.
            impact_coef: Square-root impact coefficient.
            max_slippage_pct: Ceiling on modelled slippage.
            read_env_overrides: Whether per-symbol ``*_<ROOT>`` env
                overrides are consulted.
        """
        self.profile = resolve_cost_profile(profile)
        self.base_maker_fee_pct = base_maker_fee_pct
        self.base_taker_fee_pct = base_taker_fee_pct
        self.base_slippage_pct = base_slippage_pct
        self.vol_coef = vol_coef
        self.impact_coef = impact_coef
        self.max_slippage_pct = max_slippage_pct
        self.read_env_overrides = read_env_overrides
        self._cache: Dict[str, SymbolCosts] = {}

    # -- construction ---------------------------------------------------

    @classmethod
    def from_env(
        cls,
        slippage_pct: Optional[float] = None,
        taker_fee_pct: Optional[float] = None,
        maker_fee_pct: Optional[float] = None,
        profile: Optional[str] = None,
    ) -> "CostTable":
        """Build a table from the environment plus caller-supplied bases.

        The caller-supplied values (which the engine sources from
        ``config.backtest_*``, i.e. from ``BACKTEST_SLIPPAGE_PCT`` /
        ``BACKTEST_TAKER_FEE_PCT`` / ``BACKTEST_MAKER_FEE_PCT``) are the
        global base rates for the ``legacy`` profile.

        Under ``pacifica`` those three global knobs are ignored: the base
        fees are the published schedule and slippage is modelled. Per
        symbol, ``BACKTEST_{TAKER,MAKER}_FEE_PCT_<ROOT>``,
        ``BACKTEST_HALF_SPREAD_PCT_<ROOT>`` and
        ``BACKTEST_SLIPPAGE_PCT_<ROOT>`` override in either profile - the
        shipped ``.env`` pins the globals at their legacy values, so
        honouring them under ``pacifica`` would silently defeat the
        profile.

        Args:
            slippage_pct: Global flat slippage from the caller.
            taker_fee_pct: Global taker fee from the caller.
            maker_fee_pct: Global maker fee from the caller.
            profile: Profile override; ``None`` reads the environment.

        Returns:
            A configured :class:`CostTable`.
        """
        resolved_profile = resolve_cost_profile(profile)

        base_slip = (
            slippage_pct if slippage_pct is not None else LEGACY_SLIPPAGE_PCT
        )
        base_taker = (
            taker_fee_pct if taker_fee_pct is not None else LEGACY_TAKER_FEE_PCT
        )
        base_maker = (
            maker_fee_pct if maker_fee_pct is not None else LEGACY_MAKER_FEE_PCT
        )

        if resolved_profile == PROFILE_PACIFICA:
            base_taker = PACIFICA_TAKER_FEE_PCT
            base_maker = PACIFICA_MAKER_FEE_PCT

        return cls(
            profile=resolved_profile,
            base_maker_fee_pct=base_maker,
            base_taker_fee_pct=base_taker,
            base_slippage_pct=base_slip,
            vol_coef=_env_float(
                "BACKTEST_SLIPPAGE_VOL_COEF", DEFAULT_VOL_COEF, maximum=5.0
            ),
            impact_coef=_env_float(
                "BACKTEST_SLIPPAGE_IMPACT_COEF", DEFAULT_IMPACT_COEF, maximum=5.0
            ),
            max_slippage_pct=_env_float(
                "BACKTEST_MAX_SLIPPAGE_PCT", DEFAULT_MAX_SLIPPAGE_PCT
            ),
        )

    # -- resolution -----------------------------------------------------

    def for_symbol(self, symbol: str) -> SymbolCosts:
        """Resolve the cost parameters for ``symbol`` (cached).

        Args:
            symbol: Market symbol, e.g. "BTC-USDC".

        Returns:
            The resolved :class:`SymbolCosts`.
        """
        root = symbol_root(symbol)
        cached = self._cache.get(root)
        if cached is not None:
            return cached
        resolved = self._resolve(root)
        self._cache[root] = resolved
        return resolved

    def _resolve(self, root: str) -> SymbolCosts:
        """Build the :class:`SymbolCosts` for one symbol root."""
        maker = self.base_maker_fee_pct
        taker = self.base_taker_fee_pct
        half_spread = DEFAULT_HALF_SPREAD_PCT.get(root, FALLBACK_HALF_SPREAD_PCT)
        flat_slippage: Optional[float] = (
            self.base_slippage_pct if self.profile == PROFILE_LEGACY else None
        )

        if self.read_env_overrides and root:
            maker = _env_float(f"BACKTEST_MAKER_FEE_PCT_{root}", maker)
            taker = _env_float(f"BACKTEST_TAKER_FEE_PCT_{root}", taker)
            half_spread = _env_float(
                f"BACKTEST_HALF_SPREAD_PCT_{root}", half_spread
            )
            per_symbol_flat = os.getenv(f"BACKTEST_SLIPPAGE_PCT_{root}")
            if per_symbol_flat is not None and per_symbol_flat != "":
                flat_slippage = _env_float(
                    f"BACKTEST_SLIPPAGE_PCT_{root}",
                    flat_slippage
                    if flat_slippage is not None
                    else self.base_slippage_pct,
                )

        return SymbolCosts(
            symbol=root,
            maker_fee_pct=maker,
            taker_fee_pct=taker,
            half_spread_pct=half_spread,
            vol_coef=self.vol_coef,
            impact_coef=self.impact_coef,
            max_slippage_pct=self.max_slippage_pct,
            flat_slippage_pct=flat_slippage,
        )

    # -- queries --------------------------------------------------------

    def fee_pct(self, symbol: str, role: LiquidityRole) -> float:
        """Return the fee fraction charged for a fill.

        Args:
            symbol: Market symbol.
            role: Whether the fill provided or took liquidity.

        Returns:
            Fee as a fraction of notional.
        """
        return self.for_symbol(symbol).fee_pct(role)

    def slippage_pct(
        self,
        symbol: str,
        notional: float = 0.0,
        bar_range_pct: float = 0.0,
        bar_notional: float = 0.0,
    ) -> float:
        """Return the slippage fraction for an aggressive fill.

        Under the ``legacy`` profile this is a flat number and the
        arguments are ignored. Under ``pacifica`` it is::

            half_spread + vol_coef * bar_range_pct
                        + impact_coef * sqrt(participation)

        where ``participation = notional / bar_notional``. The three
        terms are, in order: crossing the book, adverse drift while the
        order is in flight (scaled by how fast the market is moving),
        and square-root market impact from the order's own size.

        Args:
            symbol: Market symbol.
            notional: Order notional in quote currency.
            bar_range_pct: (high - low) / close for the current bar.
            bar_notional: Bar volume expressed in quote currency. Zero or
                missing volume drops the impact term rather than guessing.

        Returns:
            Slippage as a positive fraction of price, capped at
            ``max_slippage_pct``.
        """
        costs = self.for_symbol(symbol)
        if costs.flat_slippage_pct is not None:
            return costs.flat_slippage_pct

        slip = costs.half_spread_pct
        if bar_range_pct > 0:
            slip += costs.vol_coef * bar_range_pct
        if notional > 0 and bar_notional > 0:
            participation = notional / bar_notional
            slip += costs.impact_coef * (participation ** 0.5)
        return min(slip, costs.max_slippage_pct)

    def describe(self, symbol: str) -> str:
        """Return a one-line human summary of a symbol's costs."""
        costs = self.for_symbol(symbol)
        if costs.flat_slippage_pct is not None:
            slip = f"flat {costs.flat_slippage_pct * 100:.4f}%"
        else:
            slip = f"dynamic from {costs.half_spread_pct * 100:.4f}%"
        return (
            f"{costs.symbol or symbol} [{self.profile}]: "
            f"maker {costs.maker_fee_pct * 100:.4f}% / "
            f"taker {costs.taker_fee_pct * 100:.4f}% / "
            f"slippage {slip}"
        )


class CostModel:
    """Adjusts a signal's take-profit for the round-trip cost it will pay.

    The exchange charges the real fees and slippage on every fill. This
    haircut is an additional, deliberately conservative reduction of the
    gross target so a strategy's RRR reflects costs before the order is
    placed. It is asymmetric by construction (winners shrink, losers do
    not), which is why charging it at the wrong rate is expensive:

    * ``legacy`` profile - one flat ``2 * taker + slippage`` drag on every
      signal regardless of symbol or order type. Preserved bit for bit so
      historic results stay comparable.
    * ``pacifica`` profile - the entry leg is charged at the rate the
      engine's own limit/market rule says it will fill at, and the exit
      leg (a resting take-profit) is charged the maker fee with no
      slippage.

    Set ``BACKTEST_COST_TP_HAIRCUT=false`` to disable the haircut
    entirely and let the per-fill charges in ``SimulatedExchange`` be the
    only cost, which removes the double count.
    """

    def __init__(
        self,
        slippage_pct: float = LEGACY_SLIPPAGE_PCT,
        taker_fee_pct: float = LEGACY_TAKER_FEE_PCT,
        maker_fee_pct: Optional[float] = None,
        table: Optional[CostTable] = None,
        profile: Optional[str] = None,
        tp_haircut: Optional[bool] = None,
    ) -> None:
        """Build a cost model.

        Args:
            slippage_pct: Global flat slippage (legacy profile).
            taker_fee_pct: Global taker fee.
            maker_fee_pct: Global maker fee; defaults to the legacy value.
            table: Pre-built cost table; built from the environment when
                omitted.
            profile: Profile override; ``None`` reads the environment.
            tp_haircut: Whether to shrink take-profits; ``None`` reads
                ``BACKTEST_COST_TP_HAIRCUT`` (default on).
        """
        self.slippage_pct = slippage_pct
        self.taker_fee_pct = taker_fee_pct
        self.maker_fee_pct = (
            maker_fee_pct if maker_fee_pct is not None else LEGACY_MAKER_FEE_PCT
        )
        self.table = table or CostTable.from_env(
            slippage_pct=slippage_pct,
            taker_fee_pct=taker_fee_pct,
            maker_fee_pct=self.maker_fee_pct,
            profile=profile,
        )
        self.profile = self.table.profile
        # Kept as a public attribute: the historic flat round-trip figure,
        # still what the legacy profile charges and what reports quote.
        self.round_trip_cost_pct = (taker_fee_pct * 2) + slippage_pct
        self.tp_haircut_enabled = (
            tp_haircut
            if tp_haircut is not None
            else _env_bool("BACKTEST_COST_TP_HAIRCUT", True)
        )

    # ------------------------------------------------------------------

    def entry_role(self, signal: Signal, current_price: float) -> LiquidityRole:
        """Predict whether the entry order will make or take liquidity.

        Mirrors ``BacktestEngine._execute_signal``: an entry further than
        :data:`LIMIT_ENTRY_PRICE_GAP_PCT` from the current price is placed
        as a resting limit (maker); anything closer is a market order
        (taker).

        Args:
            signal: The signal about to be executed.
            current_price: Last traded price.

        Returns:
            The predicted :class:`LiquidityRole`.
        """
        entry = getattr(signal, "entry_price", None)
        if not entry or not current_price:
            return LiquidityRole.TAKER
        gap = abs(float(entry) - float(current_price)) / float(current_price)
        return (
            LiquidityRole.MAKER
            if gap > LIMIT_ENTRY_PRICE_GAP_PCT
            else LiquidityRole.TAKER
        )

    def round_trip_cost_for(
        self, signal: Signal, current_price: float
    ) -> float:
        """Return the round-trip cost fraction charged against a signal.

        Args:
            signal: The signal about to be executed.
            current_price: Last traded price.

        Returns:
            Round-trip cost as a fraction of price.
        """
        if self.profile == PROFILE_LEGACY:
            return self.round_trip_cost_pct

        symbol = getattr(signal, "asset", "") or ""
        role = self.entry_role(signal, current_price)
        entry_cost = self.table.fee_pct(symbol, role)
        if role == LiquidityRole.TAKER:
            entry_cost += self.table.slippage_pct(symbol)
        # The take-profit this haircut applies to is always placed as a
        # resting limit order by the engine, so the exit leg is a maker
        # fill with no adverse slippage.
        exit_cost = self.table.fee_pct(symbol, LiquidityRole.MAKER)
        return entry_cost + exit_cost

    def apply(self, signal: Signal, current_price: float) -> None:
        """Shrink the signal's take-profit by its round-trip cost.

        Signals below break-even after costs are flagged (not blocked --
        RiskManager handles final go/no-go).

        Args:
            signal: Signal to adjust in place.
            current_price: Last traded price.
        """
        if not (signal.take_profit and signal.stop_loss):
            return

        risk = abs(current_price - signal.stop_loss)
        gross_rr = (
            abs(signal.take_profit - current_price) / risk if risk else 0.0
        )

        if self.tp_haircut_enabled:
            cost_drag = (
                self.round_trip_cost_for(signal, current_price) * current_price
            )
            signal.take_profit = (
                signal.take_profit - cost_drag
                if signal.take_profit > current_price
                else signal.take_profit + cost_drag
            )

        if gross_rr < 1.0:
            logger.debug(
                f"Low RRR signal from {signal.strategy}: gross_rr={gross_rr:.2f}"
            )
