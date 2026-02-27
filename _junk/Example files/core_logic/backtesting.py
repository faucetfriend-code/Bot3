"""
Backtesting Framework for Trading Bot.
Simulates trading strategies on historical data with realistic conditions.
"""

from typing import List, Dict, Optional, Any, Tuple, Callable
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import csv
import numpy as np
import json

from config import get_config
from models import MarketData, Signal, OrderSide
from strategy import get_strategy_signals
from risk import validate_signal_comprehensive, RiskViolationError
from database import DatabaseManager
from loguru import logger


class BacktestResult(Enum):
    """Backtest execution result."""
    SUCCESS = "success"
    FAILED = "failed"
    ERROR = "error"


@dataclass
class BacktestConfig:
    """Configuration for backtesting."""
    symbol: str
    start_date: datetime
    end_date: datetime
    initial_balance: float = 10000.0
    commission_per_trade: float = 0.001  # 0.1%
    slippage_model: str = "fixed"  # "fixed", "percentage", "none"
    slippage_amount: float = 0.0005  # 0.05%
    enable_risk_management: bool = True
    max_concurrent_positions: int = 3
    enable_fractional_shares: bool = True
    data_source: str = "database"  # "database", "csv", "api"
    csv_file_path: Optional[str] = None  # Path to CSV file when data_source is "csv"


@dataclass
class BacktestPosition:
    """Position during backtesting."""
    symbol: str
    side: OrderSide
    quantity: float
    entry_price: float
    entry_time: datetime
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    current_price: float = 0.0
    unrealized_pnl: float = 0.0

    def update_pnl(self, current_price: float):
        """Update unrealized P&L."""
        self.current_price = current_price
        if self.side == OrderSide.BUY:
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity


@dataclass
class BacktestTrade:
    """Completed trade during backtesting."""
    symbol: str
    side: OrderSide
    quantity: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    commission: float
    strategy: str = ""
    notes: str = ""


@dataclass
class BacktestMetrics:
    """Performance metrics from backtesting."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_commission: float = 0.0
    net_pnl: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    avg_trade_pnl: float = 0.0
    avg_win_pnl: float = 0.0
    avg_loss_pnl: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    recovery_factor: float = 0.0
    start_balance: float = 0.0
    end_balance: float = 0.0
    total_return_pct: float = 0.0
    # Additional advanced metrics
    volatility: float = 0.0  # Annualized volatility
    alpha: float = 0.0  # Risk-adjusted return vs benchmark
    beta: float = 0.0  # Market correlation
    information_ratio: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_holding_period: float = 0.0  # Average trade duration in days
    best_day_pnl: float = 0.0
    worst_day_pnl: float = 0.0
    total_trading_days: int = 0
    profitable_days: int = 0
    daily_win_rate: float = 0.0


@dataclass
class BacktestReport:
    """Complete backtest report."""
    config: BacktestConfig
    metrics: BacktestMetrics
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[Tuple[datetime, float]] = field(default_factory=list)
    drawdown_curve: List[Tuple[datetime, float]] = field(default_factory=list)
    execution_time: float = 0.0
    result: BacktestResult = BacktestResult.SUCCESS
    error_message: str = ""


class BacktestingEngine:
    """
    Backtesting engine that simulates trading strategies on historical data.
    """

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.config = None
        self.market_data = []
        self.positions = {}
        self.trades = []
        self.equity_curve = []
        self.balance = 0.0
        self.current_time = None

    async def run_backtest(
        self,
        config: BacktestConfig,
        strategy_func: Optional[Callable] = None
    ) -> BacktestReport:
        """
        Run a backtest with the given configuration.

        Args:
            config: Backtest configuration
            strategy_func: Optional custom strategy function

        Returns:
            Complete backtest report
        """
        start_time = datetime.now()
        self.config = config

        try:
            logger.info(f"Starting backtest for {config.symbol} from {config.start_date} to {config.end_date}")

            # Load historical data
            self.market_data = await self._load_historical_data(config)
            if not self.market_data:
                raise ValueError(f"No historical data found for {config.symbol}")

            logger.info(f"Loaded {len(self.market_data)} data points")

            # Initialize backtest state
            self._initialize_backtest(config)

            # Run simulation
            await self._run_simulation(strategy_func or get_strategy_signals)

            # Calculate metrics
            metrics = self._calculate_metrics()

            # Create report
            report = BacktestReport(
                config=config,
                metrics=metrics,
                trades=self.trades.copy(),
                equity_curve=self.equity_curve.copy(),
                execution_time=(datetime.now() - start_time).total_seconds(),
                result=BacktestResult.SUCCESS
            )

            logger.info(f"Backtest completed. Total P&L: ${metrics.total_pnl:.2f}, Win Rate: {metrics.win_rate:.1%}")
            return report

        except Exception as e:
            logger.error(f"Backtest failed: {e}")
            return BacktestReport(
                config=config,
                metrics=BacktestMetrics(),
                execution_time=(datetime.now() - start_time).total_seconds(),
                result=BacktestResult.ERROR,
                error_message=str(e)
            )

    async def _load_historical_data(self, config: BacktestConfig) -> List[MarketData]:
        """Load historical market data for backtesting."""
        if config.data_source == "database":
            # Load from database
            db_data = self.db.get_market_data(
                symbol=config.symbol,
                start_date=config.start_date,
                end_date=config.end_date
            )

            market_data = []
            for data in db_data:
                market_data.append(MarketData(
                    symbol=config.symbol,
                    timeframe="1h",  # Assume hourly data
                    timestamp=datetime.fromisoformat(data["timestamp"]),
                    open=float(data["price"]),
                    high=float(data["price"]),
                    low=float(data["price"]),
                    close=float(data["price"]),
                    volume=data.get("volume", 0)
                ))

            # Sort by timestamp
            market_data.sort(key=lambda x: x.timestamp)
            return market_data

        elif config.data_source == "csv":
            # Load from CSV file
            if not config.csv_file_path:
                raise ValueError("csv_file_path must be specified when data_source is 'csv'")

            csv_path = Path(config.csv_file_path)
            if not csv_path.exists():
                raise FileNotFoundError(f"CSV file not found: {csv_path}")

            market_data = self._load_csv_data(csv_path, config.symbol)
            return market_data

        else:
            raise ValueError(f"Unsupported data source: {config.data_source}")

    def _initialize_backtest(self, config: BacktestConfig):
        """Initialize backtest state."""
        self.balance = config.initial_balance
        self.positions = {}
        self.trades = []
        self.equity_curve = [(config.start_date, config.initial_balance)]
        self.current_time = config.start_date

    async def _run_simulation(self, strategy_func: Callable):
        """Run the trading simulation."""
        for i, data_point in enumerate(self.market_data):
            self.current_time = data_point.timestamp

            # Update existing positions with current price
            self._update_positions(data_point.close)

            # Generate signals using strategy
            try:
                signals = await strategy_func([data_point], config=get_config())
                signals = [s for s in signals if s.symbol == self.config.symbol]
            except Exception as e:
                logger.warning(f"Strategy error at {self.current_time}: {e}")
                signals = []

            # Process signals
            for signal in signals:
                await self._process_signal(signal, data_point)

            # Record equity curve point
            total_equity = self.balance + sum(pos.unrealized_pnl for pos in self.positions.values())
            self.equity_curve.append((self.current_time, total_equity))

            # Progress logging
            if i % 100 == 0:
                logger.debug(f"Processed {i}/{len(self.market_data)} data points")

    def _update_positions(self, current_price: float):
        """Update all positions with current price."""
        for position in self.positions.values():
            position.update_pnl(current_price)

    async def _process_signal(self, signal: Signal, market_data: MarketData):
        """Process a trading signal."""
        try:
            # Validate signal with risk management
            if self.config.enable_risk_management:
                is_valid, reason = validate_signal_comprehensive(signal, [market_data])
                if not is_valid:
                    logger.debug(f"Signal rejected: {reason}")
                    return

            # Check position limits
            if len(self.positions) >= self.config.max_concurrent_positions:
                logger.debug("Maximum concurrent positions reached")
                return

            # Execute signal
            if signal.signal_type in ["buy", "long"]:
                await self._execute_buy_signal(signal, market_data)
            elif signal.signal_type in ["sell", "short"]:
                await self._execute_sell_signal(signal, market_data)

        except RiskViolationError as e:
            logger.debug(f"Risk violation: {e}")
        except Exception as e:
            logger.error(f"Error processing signal: {e}")

    async def _execute_buy_signal(self, signal: Signal, market_data: MarketData):
        """Execute a buy signal."""
        # Calculate position size (simplified)
        position_size = self._calculate_position_size(signal, market_data)

        if position_size <= 0:
            return

        # Apply slippage
        entry_price = self._apply_slippage(market_data.close, "buy")

        # Create position
        position = BacktestPosition(
            symbol=signal.symbol,
            side=OrderSide.BUY,
            quantity=position_size,
            entry_price=entry_price,
            entry_time=self.current_time,
            current_price=entry_price
        )

        # Calculate commission
        commission = position_size * entry_price * self.config.commission_per_trade

        # Update balance
        cost = (position_size * entry_price) + commission
        if cost > self.balance:
            logger.debug("Insufficient balance for trade")
            return

        self.balance -= cost
        self.positions[signal.symbol] = position

        logger.debug(f"Opened long position: {position_size} {signal.symbol} @ ${entry_price:.2f}")

    async def _execute_sell_signal(self, signal: Signal, market_data: MarketData):
        """Execute a sell signal."""
        # Check if we have a position to close
        if signal.symbol not in self.positions:
            return

        position = self.positions[signal.symbol]

        # Apply slippage
        exit_price = self._apply_slippage(market_data.close, "sell")

        # Calculate P&L
        if position.side == OrderSide.BUY:
            pnl = (exit_price - position.entry_price) * position.quantity
        else:
            pnl = (position.entry_price - exit_price) * position.quantity

        # Calculate commission
        commission = position.quantity * exit_price * self.config.commission_per_trade

        # Create trade record
        trade = BacktestTrade(
            symbol=signal.symbol,
            side=position.side,
            quantity=position.quantity,
            entry_price=position.entry_price,
            exit_price=exit_price,
            entry_time=position.entry_time,
            exit_time=self.current_time,
            pnl=pnl,
            commission=commission,
            strategy=getattr(signal, 'strategy', ''),
            notes=getattr(signal, 'notes', '')
        )

        # Update balance
        self.balance += (position.quantity * exit_price) - commission + pnl

        # Remove position
        del self.positions[signal.symbol]

        # Record trade
        self.trades.append(trade)

        logger.debug(f"Closed position: {signal.symbol} P&L: ${pnl:.2f}")

    def _calculate_position_size(self, signal: Signal, market_data: MarketData) -> float:
        """Calculate position size based on signal and risk management."""
        # Simplified position sizing - risk 1% of current balance per trade
        risk_amount = self.balance * 0.01

        # Assume 2% stop loss
        stop_distance = market_data.close * 0.02
        position_size = risk_amount / stop_distance

        # Limit to available balance
        max_position_value = self.balance * 0.1  # Max 10% of balance per position
        max_position_size = max_position_value / market_data.close

        return min(position_size, max_position_size)

    def _apply_slippage(self, price: float, side: str) -> float:
        """Apply slippage to price."""
        if self.config.slippage_model == "none":
            return price
        elif self.config.slippage_model == "fixed":
            slippage = self.config.slippage_amount
        elif self.config.slippage_model == "percentage":
            slippage = price * self.config.slippage_amount
        else:
            return price

        return price + slippage if side == "buy" else price - slippage

    def _load_csv_data(self, csv_path: Path, symbol: str) -> List[MarketData]:
        """
        Load market data from CSV file with auto-detection of format.

        Args:
            csv_path: Path to CSV file
            symbol: Trading symbol

        Returns:
            List of MarketData objects

        Raises:
            ValueError: If CSV format is unsupported or data is invalid
        """
        # Detect CSV format
        csv_format = self._detect_csv_format(csv_path)

        market_data = []
        with open(csv_path, 'r', newline='', encoding='utf-8') as csvfile:
            if csv_format == "yahoo":
                market_data = self._parse_yahoo_csv(csvfile, symbol)
            elif csv_format == "custom":
                market_data = self._parse_custom_csv(csvfile, symbol)
            else:
                raise ValueError(f"Unsupported CSV format: {csv_format}")

        # Validate and sort data
        if not market_data:
            raise ValueError("No valid market data found in CSV file")

        # Validate market data
        self._validate_market_data(market_data)

        market_data.sort(key=lambda x: x.timestamp)
        logger.info(f"Loaded {len(market_data)} market data points from {csv_path}")

        return market_data

    def _detect_csv_format(self, csv_path: Path) -> str:
        """
        Auto-detect CSV format by examining headers and sample data.

        Args:
            csv_path: Path to CSV file

        Returns:
            Format type: "yahoo", "custom", or raises ValueError
        """
        with open(csv_path, 'r', newline='', encoding='utf-8') as csvfile:
            sample = csvfile.read(1024)  # Read first 1KB
            csvfile.seek(0)  # Reset file pointer

            # Check for Yahoo Finance format
            if 'Date,Open,High,Low,Close,Adj Close,Volume' in sample:
                return "yahoo"

            # Check for custom format with OHLCV columns
            lines = sample.split('\n')[:5]  # Check first 5 lines
            if len(lines) >= 2:
                header = lines[0].lower()
                if ('open' in header and 'high' in header and 'low' in header and
                    'close' in header and 'volume' in header):
                    return "custom"

        raise ValueError(f"Unable to detect CSV format for {csv_path}. Supported formats: Yahoo Finance, Custom OHLCV")

    def _parse_yahoo_csv(self, csvfile, symbol: str) -> List[MarketData]:
        """
        Parse Yahoo Finance CSV format.

        Expected columns: Date,Open,High,Low,Close,Adj Close,Volume
        """
        reader = csv.DictReader(csvfile)
        market_data = []

        for row in reader:
            try:
                # Parse date
                timestamp = datetime.strptime(row['Date'], '%Y-%m-%d')

                market_data.append(MarketData(
                    symbol=symbol,
                    timeframe="1d",  # Assume daily data
                    timestamp=timestamp,
                    open=float(row['Open']),
                    high=float(row['High']),
                    low=float(row['Low']),
                    close=float(row['Close']),
                    volume=float(row['Volume'])
                ))
            except (ValueError, KeyError) as e:
                logger.warning(f"Skipping invalid row in Yahoo CSV: {e}")
                continue

        return market_data

    def _parse_custom_csv(self, csvfile, symbol: str) -> List[MarketData]:
        """
        Parse custom CSV format with OHLCV columns.

        Expected columns should include: timestamp/date, open, high, low, close, volume
        """
        reader = csv.DictReader(csvfile)
        market_data = []

        # Normalize column names to lowercase
        fieldnames = [name.lower() for name in reader.fieldnames or []]

        for row in reader:
            try:
                # Try different timestamp column names
                timestamp_str = None
                for col_name in ['timestamp', 'date', 'datetime', 'time']:
                    if col_name in row:
                        timestamp_str = row[col_name]
                        break

                if not timestamp_str:
                    raise ValueError("No timestamp/date column found")

                # Parse timestamp - try multiple formats
                timestamp = None
                date_formats = [
                    '%Y-%m-%d %H:%M:%S',
                    '%Y-%m-%d',
                    '%m/%d/%Y',
                    '%d/%m/%Y',
                    '%Y/%m/%d'
                ]

                for fmt in date_formats:
                    try:
                        timestamp = datetime.strptime(timestamp_str, fmt)
                        break
                    except ValueError:
                        continue

                if timestamp is None:
                    raise ValueError(f"Unable to parse timestamp: {timestamp_str}")

                market_data.append(MarketData(
                    symbol=symbol,
                    timeframe="1h",  # Assume hourly data for custom format
                    timestamp=timestamp,
                    open=float(row.get('open', row.get('Open', 0))),
                    high=float(row.get('high', row.get('High', 0))),
                    low=float(row.get('low', row.get('Low', 0))),
                    close=float(row.get('close', row.get('Close', 0))),
                    volume=float(row.get('volume', row.get('Volume', 0)))
                ))
            except (ValueError, KeyError) as e:
                logger.warning(f"Skipping invalid row in custom CSV: {e}")
                continue

        return market_data

    def _validate_market_data(self, market_data: List[MarketData]):
        """
        Validate loaded market data for consistency and correctness.

        Args:
            market_data: List of MarketData objects to validate

        Raises:
            ValueError: If data validation fails
        """
        if not market_data:
            raise ValueError("Market data list is empty")

        errors = []

        for i, data in enumerate(market_data):
            # Check for valid OHLC values
            if data.open <= 0:
                errors.append(f"Row {i}: Invalid open price {data.open}")
            if data.high <= 0:
                errors.append(f"Row {i}: Invalid high price {data.high}")
            if data.low <= 0:
                errors.append(f"Row {i}: Invalid low price {data.low}")
            if data.close <= 0:
                errors.append(f"Row {i}: Invalid close price {data.close}")
            if data.volume < 0:
                errors.append(f"Row {i}: Invalid volume {data.volume}")

            # Check OHLC relationships
            if data.high < max(data.open, data.close):
                errors.append(f"Row {i}: High price {data.high} is less than open {data.open} or close {data.close}")
            if data.low > min(data.open, data.close):
                errors.append(f"Row {i}: Low price {data.low} is greater than open {data.open} or close {data.close}")

        # Check for chronological order (after sorting)
        for i in range(1, len(market_data)):
            if market_data[i].timestamp <= market_data[i-1].timestamp:
                errors.append(f"Non-chronological timestamps at rows {i-1} and {i}")

        # Check for reasonable time gaps (no gaps larger than 1 week for daily data, 1 day for hourly)
        for i in range(1, len(market_data)):
            time_diff = market_data[i].timestamp - market_data[i-1].timestamp
            if market_data[i].timeframe == "1d" and time_diff.days > 7:
                errors.append(f"Large time gap at row {i}: {time_diff.days} days")
            elif market_data[i].timeframe == "1h" and time_diff.days > 1:
                errors.append(f"Large time gap at row {i}: {time_diff}")

        if errors:
            error_msg = f"Market data validation failed with {len(errors)} errors:\n" + "\n".join(errors[:10])  # Show first 10 errors
            if len(errors) > 10:
                error_msg += f"\n... and {len(errors) - 10} more errors"
            raise ValueError(error_msg)

    def _calculate_metrics(self) -> BacktestMetrics:
        """Calculate comprehensive performance metrics."""
        if not self.trades:
            return BacktestMetrics()

        metrics = BacktestMetrics()
        metrics.total_trades = len(self.trades)
        metrics.start_balance = self.config.initial_balance
        metrics.end_balance = self.balance

        # Calculate trade statistics
        pnl_values = [trade.pnl for trade in self.trades]
        winning_trades = [t for t in self.trades if t.pnl > 0]
        losing_trades = [t for t in self.trades if t.pnl <= 0]

        metrics.winning_trades = len(winning_trades)
        metrics.losing_trades = len(losing_trades)
        metrics.win_rate = metrics.winning_trades / metrics.total_trades if metrics.total_trades > 0 else 0

        metrics.total_pnl = sum(pnl_values)
        metrics.total_commission = sum(trade.commission for trade in self.trades)
        metrics.net_pnl = metrics.total_pnl - metrics.total_commission

        if winning_trades:
            metrics.avg_win_pnl = np.mean([t.pnl for t in winning_trades])
            metrics.largest_win = max(t.pnl for t in winning_trades)

        if losing_trades:
            metrics.avg_loss_pnl = np.mean([t.pnl for t in losing_trades])
            metrics.largest_loss = min(t.pnl for t in losing_trades)

        if metrics.total_trades > 0:
            metrics.avg_trade_pnl = metrics.total_pnl / metrics.total_trades

        # Calculate profit factor
        total_wins = sum(t.pnl for t in winning_trades)
        total_losses = abs(sum(t.pnl for t in losing_trades))
        metrics.profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')

        # Calculate expectancy
        win_rate = metrics.win_rate
        avg_win = metrics.avg_win_pnl if winning_trades else 0
        avg_loss = abs(metrics.avg_loss_pnl) if losing_trades else 0
        metrics.expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        # Calculate drawdown
        equity_values = [point[1] for point in self.equity_curve]
        peak = metrics.start_balance
        max_drawdown = 0

        for equity in equity_values:
            if equity > peak:
                peak = equity
            drawdown = peak - equity
            max_drawdown = max(max_drawdown, drawdown)

        metrics.max_drawdown = max_drawdown
        metrics.max_drawdown_pct = max_drawdown / peak if peak > 0 else 0

        # Calculate recovery factor
        metrics.recovery_factor = metrics.net_pnl / max_drawdown if max_drawdown > 0 else float('inf')

        # Calculate return percentage
        metrics.total_return_pct = (metrics.end_balance - metrics.start_balance) / metrics.start_balance

        # Calculate Sharpe ratio (simplified - assumes daily returns)
        if len(equity_values) > 1:
            returns = np.diff(equity_values) / equity_values[:-1]
            if len(returns) > 0:
                avg_return = np.mean(returns)
                std_return = np.std(returns)
                if std_return > 0:
                    metrics.sharpe_ratio = avg_return / std_return * np.sqrt(252)  # Annualized
                    metrics.volatility = std_return * np.sqrt(252)  # Annualized volatility

                # Calculate Sortino ratio (downside deviation)
                negative_returns = returns[returns < 0]
                if len(negative_returns) > 0:
                    downside_std = np.std(negative_returns)
                    if downside_std > 0:
                        metrics.sortino_ratio = avg_return / downside_std * np.sqrt(252)

        # Calculate Calmar ratio
        if metrics.max_drawdown > 0:
            metrics.calmar_ratio = metrics.total_return_pct / metrics.max_drawdown_pct

        # Calculate additional metrics
        if self.trades:
            # Holding periods
            holding_periods = [
                (trade.exit_time - trade.entry_time).total_seconds() / (24 * 3600)
                for trade in self.trades
            ]
            metrics.avg_holding_period = np.mean(holding_periods) if holding_periods else 0

            # Consecutive wins/losses
            consecutive_wins = 0
            consecutive_losses = 0
            current_wins = 0
            current_losses = 0

            for trade in self.trades:
                if trade.pnl > 0:
                    current_wins += 1
                    current_losses = 0
                    consecutive_wins = max(consecutive_wins, current_wins)
                else:
                    current_losses += 1
                    current_wins = 0
                    consecutive_losses = max(consecutive_losses, current_losses)

            metrics.max_consecutive_wins = consecutive_wins
            metrics.max_consecutive_losses = consecutive_losses

        # Daily performance metrics
        if len(self.equity_curve) > 1:
            daily_returns = []
            current_day = None
            day_start_equity = None

            for timestamp, equity in self.equity_curve:
                day = timestamp.date()
                if current_day != day:
                    if day_start_equity is not None and current_day is not None:
                        daily_return = (equity - day_start_equity) / day_start_equity
                        daily_returns.append(daily_return)
                    current_day = day
                    day_start_equity = equity

            if day_start_equity is not None and current_day is not None:
                daily_return = (self.equity_curve[-1][1] - day_start_equity) / day_start_equity
                daily_returns.append(daily_return)

            if daily_returns:
                metrics.total_trading_days = len(daily_returns)
                metrics.profitable_days = sum(1 for r in daily_returns if r > 0)
                metrics.daily_win_rate = metrics.profitable_days / metrics.total_trading_days
                metrics.best_day_pnl = max(daily_returns) * metrics.start_balance
                metrics.worst_day_pnl = min(daily_returns) * metrics.start_balance

        return metrics


# Utility functions
async def run_backtest(
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    db: DatabaseManager,
    **kwargs
) -> BacktestReport:
    """
    Convenience function to run a backtest.

    Args:
        symbol: Trading symbol
        start_date: Start date for backtest
        end_date: End date for backtest
        db: Database manager instance
        **kwargs: Additional backtest configuration

    Returns:
        Backtest report
    """
    config = BacktestConfig(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        **kwargs
    )

    engine = BacktestingEngine(db)
    return await engine.run_backtest(config)


def save_backtest_report(report: BacktestReport, filepath: str):
    """Save comprehensive backtest report to JSON file."""
    report_dict = {
        "config": {
            "symbol": report.config.symbol,
            "start_date": report.config.start_date.isoformat(),
            "end_date": report.config.end_date.isoformat(),
            "initial_balance": report.config.initial_balance,
            "commission_per_trade": report.config.commission_per_trade,
            "slippage_model": report.config.slippage_model,
            "slippage_amount": report.config.slippage_amount,
            "enable_risk_management": report.config.enable_risk_management,
            "max_concurrent_positions": report.config.max_concurrent_positions,
            "enable_fractional_shares": report.config.enable_fractional_shares,
        },
        "metrics": {
            # Core metrics
            "total_trades": report.metrics.total_trades,
            "winning_trades": report.metrics.winning_trades,
            "losing_trades": report.metrics.losing_trades,
            "win_rate": report.metrics.win_rate,
            "total_pnl": report.metrics.total_pnl,
            "net_pnl": report.metrics.net_pnl,
            "max_drawdown": report.metrics.max_drawdown,
            "max_drawdown_pct": report.metrics.max_drawdown_pct,
            "sharpe_ratio": report.metrics.sharpe_ratio,
            "sortino_ratio": report.metrics.sortino_ratio,
            "calmar_ratio": report.metrics.calmar_ratio,
            "total_return_pct": report.metrics.total_return_pct,
            # Trade analysis
            "avg_trade_pnl": report.metrics.avg_trade_pnl,
            "avg_win_pnl": report.metrics.avg_win_pnl,
            "avg_loss_pnl": report.metrics.avg_loss_pnl,
            "largest_win": report.metrics.largest_win,
            "largest_loss": report.metrics.largest_loss,
            "profit_factor": report.metrics.profit_factor,
            "expectancy": report.metrics.expectancy,
            "recovery_factor": report.metrics.recovery_factor,
            # Risk metrics
            "volatility": report.metrics.volatility,
            "alpha": report.metrics.alpha,
            "beta": report.metrics.beta,
            "information_ratio": report.metrics.information_ratio,
            # Trading statistics
            "max_consecutive_wins": report.metrics.max_consecutive_wins,
            "max_consecutive_losses": report.metrics.max_consecutive_losses,
            "avg_holding_period": report.metrics.avg_holding_period,
            "best_day_pnl": report.metrics.best_day_pnl,
            "worst_day_pnl": report.metrics.worst_day_pnl,
            "total_trading_days": report.metrics.total_trading_days,
            "profitable_days": report.metrics.profitable_days,
            "daily_win_rate": report.metrics.daily_win_rate,
        },
        "trades_summary": [
            {
                "symbol": trade.symbol,
                "side": trade.side.value if hasattr(trade.side, 'value') else str(trade.side),
                "entry_time": trade.entry_time.isoformat(),
                "exit_time": trade.exit_time.isoformat(),
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "pnl": trade.pnl,
                "commission": trade.commission,
                "strategy": trade.strategy,
            }
            for trade in report.trades[:100]  # Limit to first 100 trades for summary
        ],
        "equity_curve": [
            {"timestamp": ts.isoformat(), "equity": eq}
            for ts, eq in report.equity_curve
        ],
        "execution_time": report.execution_time,
        "result": report.result.value,
        "error_message": report.error_message,
        "generated_at": datetime.now().isoformat(),
    }

    with open(filepath, 'w') as f:
        json.dump(report_dict, f, indent=2, default=str)

    logger.info(f"Backtest report saved to {filepath}")

    # Also save a human-readable summary
    summary_filepath = filepath.replace('.json', '_summary.txt')
    with open(summary_filepath, 'w') as f:
        f.write(f"Backtest Report for {report.config.symbol}\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"Period: {report.config.start_date.date()} to {report.config.end_date.date()}\n")
        f.write(f"Initial Balance: ${report.config.initial_balance:,.2f}\n")
        f.write(f"Final Balance: ${report.metrics.end_balance:,.2f}\n")
        f.write(f"Total Return: {report.metrics.total_return_pct:.2%}\n\n")

        f.write("Performance Metrics:\n")
        f.write(f"  Total Trades: {report.metrics.total_trades}\n")
        f.write(f"  Win Rate: {report.metrics.win_rate:.1%}\n")
        f.write(f"  Total P&L: ${report.metrics.total_pnl:,.2f}\n")
        f.write(f"  Net P&L: ${report.metrics.net_pnl:,.2f}\n")
        f.write(f"  Max Drawdown: ${report.metrics.max_drawdown:,.2f} ({report.metrics.max_drawdown_pct:.2%})\n")
        f.write(f"  Sharpe Ratio: {report.metrics.sharpe_ratio:.2f}\n")
        f.write(f"  Sortino Ratio: {report.metrics.sortino_ratio:.2f}\n")
        f.write(f"  Calmar Ratio: {report.metrics.calmar_ratio:.2f}\n\n")

        f.write("Risk Metrics:\n")
        f.write(f"  Profit Factor: {report.metrics.profit_factor:.2f}\n")
        f.write(f"  Expectancy: ${report.metrics.expectancy:,.2f}\n")
        f.write(f"  Recovery Factor: {report.metrics.recovery_factor:.2f}\n")
        f.write(f"  Volatility: {report.metrics.volatility:.2%}\n\n")

        f.write("Trading Statistics:\n")
        f.write(f"  Average Trade P&L: ${report.metrics.avg_trade_pnl:,.2f}\n")
        f.write(f"  Average Holding Period: {report.metrics.avg_holding_period:.1f} days\n")
        f.write(f"  Max Consecutive Wins: {report.metrics.max_consecutive_wins}\n")
        f.write(f"  Max Consecutive Losses: {report.metrics.max_consecutive_losses}\n")
        f.write(f"  Daily Win Rate: {report.metrics.daily_win_rate:.1%}\n\n")

        f.write(f"Execution Time: {report.execution_time:.2f} seconds\n")
        f.write(f"Result: {report.result.value}\n")

    logger.info(f"Backtest summary saved to {summary_filepath}")


def load_backtest_report(filepath: str) -> Dict[str, Any]:
    """Load backtest report from JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)
