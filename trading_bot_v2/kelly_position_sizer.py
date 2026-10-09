"""
Kelly Criterion Position Sizing Module

Implements Kelly Criterion-based position sizing with safety features:
- Calculates Kelly % from last 50 trades per strategy
- Uses fractional Kelly (0.5x or 0.25x) for risk management
- Falls back to fixed percentages if insufficient trade history
- Applies maximum position size limits (10% account cap)

Formula: Position size = (Win rate × Avg win - Loss rate × Avg loss) ÷ Avg win
"""

from typing import Dict, Optional
from loguru import logger
from .models import Signal
from .config import StrategyType
from .database import DatabaseManager
from .history import TradeStore


class KellyPositionSizer:
    """
    Kelly Criterion-based position sizing with safety features.

    Uses historical trade performance to calculate optimal position sizes
    while maintaining strict risk management limits.
    """

    def __init__(
        self,
        db: DatabaseManager,
        kelly_fraction: float = 0.5,
        min_trades: int = 50,
        trade_store: Optional[TradeStore] = None,
    ):
        """
        Initialize Kelly position sizer.

        Args:
            db: Database instance to query trade history
            kelly_fraction: Fraction of Kelly to use (0.5 = half Kelly, safer)
            min_trades: Minimum trades needed before using Kelly (default: 50)
            trade_store: Optional history.TradeStore used for trade-history
                queries; constructed over ``db`` when omitted.
        """
        self.db = db
        self.trade_store = trade_store or TradeStore(db=db)
        self.kelly_fraction = kelly_fraction
        self.min_trades = min_trades

        # Maximum position size as % of account (hard cap)
        self.max_position_pct = 0.10  # 10% maximum

        # Fallback percentages by strategy (when insufficient history)
        self.fallback_percentages = {
            StrategyType.TREND_FOLLOWING: 0.02,  # 2%
            StrategyType.MA_CROSSOVER: 0.02,  # 2%
            StrategyType.MEAN_REVERSION: 0.015,  # 1.5%
            StrategyType.GRID_TRADING: 0.005,  # 0.5%
            StrategyType.LIQUIDATION_CAPTURE: 0.025,  # 2.5%
            StrategyType.BREAKOUT: 0.02,  # 2%
        }

        logger.info(
            f"Kelly Position Sizer initialized: "
            f"kelly_fraction={kelly_fraction}, min_trades={min_trades}"
        )

    def calculate_position_size(
        self, signal: Signal, account_balance: float, account_id: str = "sub_1"
    ) -> float:
        """
        Calculate position size using Kelly Criterion.

        Workflow:
        1. Get last 50 trades for this strategy from database
        2. If < min_trades: use fixed percentage fallback
        3. Calculate win rate, avg win, avg loss
        4. Calculate Kelly %
        5. Apply kelly_fraction (0.5x for safety)
        6. Calculate quantity based on stop loss distance
        7. Apply sanity checks (max 10% account per trade)

        Args:
            signal: Trading signal with strategy, entry, stop loss
            account_balance: Current account balance
            account_id: Account ID for multi-account support

        Returns:
            Position quantity (contracts/shares)
        """
        strategy = signal.strategy

        # Get strategy performance from database
        performance = self._get_strategy_performance(strategy, account_id)

        # Check if we have sufficient trade history
        if performance["total_trades"] < self.min_trades:
            logger.info(
                f"Insufficient trade history for {strategy.value}: "
                f"{performance['total_trades']} < {self.min_trades}. "
                f"Using fallback position sizing."
            )
            return self._get_fallback_size(strategy, account_balance, signal)

        # Calculate Kelly percentage
        kelly_pct = self._calculate_kelly_pct(
            performance["win_rate"], performance["avg_win"], performance["avg_loss"]
        )

        # Apply fractional Kelly for safety
        adjusted_kelly_pct = kelly_pct * self.kelly_fraction

        # Handle negative or zero Kelly (system has negative expectancy)
        if adjusted_kelly_pct <= 0:
            logger.warning(
                f"Negative Kelly % for {strategy.value}: {kelly_pct:.4f}. "
                f"Strategy has negative expectancy. Using 1% fallback."
            )
            adjusted_kelly_pct = 0.01  # 1% fallback

        # Apply maximum cap (10% of account)
        adjusted_kelly_pct = min(adjusted_kelly_pct, self.max_position_pct)

        # Calculate dollar risk based on Kelly %
        dollar_risk = account_balance * adjusted_kelly_pct

        # Calculate position size based on stop loss distance
        stop_distance_pct = signal.stop_distance_pct
        if stop_distance_pct <= 0:
            logger.error(
                f"Invalid stop distance: {stop_distance_pct}. Cannot calculate position size."
            )
            return 0.0

        # Position value = dollar risk / stop distance %
        position_value = dollar_risk / stop_distance_pct

        # Calculate quantity
        quantity = position_value / signal.entry_price

        # Apply minimum quantity check (at least 1 contract)
        if quantity < 1.0:
            logger.warning(
                f"Calculated quantity {quantity:.4f} < 1.0. Adjusting to 1.0 contract."
            )
            quantity = 1.0

        # Log calculation details
        logger.info(
            f"Kelly Position Sizing for {strategy.value}:\n"
            f"  Trade History: {performance['total_trades']} trades\n"
            f"  Win Rate: {performance['win_rate']:.2%}\n"
            f"  Avg Win: ${performance['avg_win']:.2f}\n"
            f"  Avg Loss: ${performance['avg_loss']:.2f}\n"
            f"  Raw Kelly %: {kelly_pct:.2%}\n"
            f"  Adjusted Kelly % (×{self.kelly_fraction}): {adjusted_kelly_pct:.2%}\n"
            f"  Dollar Risk: ${dollar_risk:.2f}\n"
            f"  Stop Distance: {stop_distance_pct:.2%}\n"
            f"  Position Value: ${position_value:.2f}\n"
            f"  Quantity: {quantity:.4f} contracts"
        )

        return quantity

    def _get_strategy_performance(
        self, strategy: StrategyType, account_id: str = "sub_1"
    ) -> Dict[str, float]:
        """
        Query database for strategy performance metrics.

        Args:
            strategy: Strategy type to analyze
            account_id: Account ID

        Returns:
            Dict with win_rate, avg_win, avg_loss, total_trades
        """
        try:
            # Query last 50 closed trades for this strategy via TradeStore
            trades = self.trade_store.get_closed_trades(
                strategy=strategy.value, account_id=account_id, limit=50
            )

            if not trades:
                logger.debug(f"No closed trades found for {strategy.value}")
                return {
                    "win_rate": 0.0,
                    "avg_win": 0.0,
                    "avg_loss": 0.0,
                    "total_trades": 0,
                }

            # Calculate metrics
            wins = []
            losses = []

            for trade in trades:
                pnl = trade["pnl"]
                if pnl > 0:
                    wins.append(pnl)
                elif pnl < 0:
                    losses.append(abs(pnl))

            total_trades = len(trades)
            win_count = len(wins)
            loss_count = len(losses)

            win_rate = win_count / total_trades if total_trades > 0 else 0.0
            avg_win = sum(wins) / win_count if win_count > 0 else 0.0
            avg_loss = sum(losses) / loss_count if loss_count > 0 else 0.0

            logger.debug(
                f"Strategy {strategy.value} performance: "
                f"{win_count}W/{loss_count}L ({win_rate:.2%}), "
                f"Avg W: ${avg_win:.2f}, Avg L: ${avg_loss:.2f}"
            )

            return {
                "win_rate": win_rate,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "total_trades": total_trades,
            }

        except Exception as e:
            logger.error(f"Error querying strategy performance: {e}")
            return {
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "total_trades": 0,
            }

    def _calculate_kelly_pct(
        self, win_rate: float, avg_win: float, avg_loss: float
    ) -> float:
        """
        Calculate Kelly percentage.

        Formula: (win_rate × avg_win - (1 - win_rate) × avg_loss) / avg_win

        Args:
            win_rate: Win rate (0-1)
            avg_win: Average winning trade ($)
            avg_loss: Average losing trade ($)

        Returns:
            Kelly percentage (0-1)
        """
        if avg_win <= 0:
            logger.warning(f"Invalid avg_win: {avg_win}. Cannot calculate Kelly %.")
            return 0.0

        loss_rate = 1.0 - win_rate

        # Kelly formula
        kelly_pct = (win_rate * avg_win - loss_rate * avg_loss) / avg_win

        logger.debug(
            f"Kelly Calculation: "
            f"({win_rate:.4f} × {avg_win:.2f} - {loss_rate:.4f} × {avg_loss:.2f}) "
            f"/ {avg_win:.2f} = {kelly_pct:.4f}"
        )

        return kelly_pct

    def _get_fallback_size(
        self, strategy: StrategyType, account_balance: float, signal: Signal
    ) -> float:
        """
        Get fallback position size when insufficient trade history.

        Uses fixed percentages:
        - Trend Following / MA Crossover: 2%
        - Mean Reversion: 1.5%
        - Grid Trading: 0.5%
        - Liquidation Capture: 2.5%
        - Breakout: 2%

        Args:
            strategy: Strategy type
            account_balance: Account balance
            signal: Trading signal

        Returns:
            Position quantity
        """
        # Get fallback percentage for strategy
        fallback_pct = self.fallback_percentages.get(strategy, 0.02)  # Default 2%

        # Calculate dollar risk
        dollar_risk = account_balance * fallback_pct

        # Calculate position size based on stop loss distance
        stop_distance_pct = signal.stop_distance_pct
        if stop_distance_pct <= 0:
            logger.error(
                f"Invalid stop distance: {stop_distance_pct}. Cannot calculate fallback size."
            )
            return 0.0

        position_value = dollar_risk / stop_distance_pct
        quantity = position_value / signal.entry_price

        # Minimum 1 contract
        if quantity < 1.0:
            quantity = 1.0

        logger.info(
            f"Fallback Position Sizing for {strategy.value}:\n"
            f"  Fallback %: {fallback_pct:.2%}\n"
            f"  Dollar Risk: ${dollar_risk:.2f}\n"
            f"  Stop Distance: {stop_distance_pct:.2%}\n"
            f"  Position Value: ${position_value:.2f}\n"
            f"  Quantity: {quantity:.4f} contracts"
        )

        return quantity

    def update_kelly_fraction(self, new_fraction: float) -> None:
        """
        Update Kelly fraction (for dynamic risk adjustment).

        Args:
            new_fraction: New Kelly fraction (e.g., 0.25 = quarter Kelly)
        """
        if not 0 < new_fraction <= 1.0:
            logger.error(
                f"Invalid Kelly fraction: {new_fraction}. Must be between 0 and 1."
            )
            return

        old_fraction = self.kelly_fraction
        self.kelly_fraction = new_fraction
        logger.info(
            f"Kelly fraction updated: {old_fraction} → {new_fraction} "
            f"({'more aggressive' if new_fraction > old_fraction else 'more conservative'})"
        )

    def get_strategy_stats(
        self, strategy: StrategyType, account_id: str = "sub_1"
    ) -> Dict[str, float]:
        """
        Get current performance stats for a strategy.

        Args:
            strategy: Strategy type
            account_id: Account ID

        Returns:
            Performance metrics dictionary
        """
        return self._get_strategy_performance(strategy, account_id)

    def get_recommended_kelly_fraction(
        self, strategy: StrategyType, account_id: str = "sub_1"
    ) -> float:
        """
        Get recommended Kelly fraction based on strategy performance stability.

        More stable strategies (higher Sharpe-like metrics) can use higher fractions.
        Volatile strategies should use lower fractions (0.25x).

        Args:
            strategy: Strategy type
            account_id: Account ID

        Returns:
            Recommended Kelly fraction (0.25-1.0)
        """
        performance = self._get_strategy_performance(strategy, account_id)

        if performance["total_trades"] < self.min_trades:
            return 0.25  # Very conservative for new strategies

        win_rate = performance["win_rate"]
        avg_win = performance["avg_win"]
        avg_loss = performance["avg_loss"]

        # Calculate profit factor
        total_wins = win_rate * performance["total_trades"] * avg_win
        total_losses = (1 - win_rate) * performance["total_trades"] * avg_loss
        profit_factor = total_wins / total_losses if total_losses > 0 else 0

        # Recommend fraction based on profit factor
        if profit_factor >= 2.0 and win_rate >= 0.55:
            # Strong performance → 0.5x Kelly (standard)
            return 0.5
        elif profit_factor >= 1.5 and win_rate >= 0.45:
            # Moderate performance → 0.33x Kelly (conservative)
            return 0.33
        else:
            # Weak performance → 0.25x Kelly (very conservative)
            return 0.25
