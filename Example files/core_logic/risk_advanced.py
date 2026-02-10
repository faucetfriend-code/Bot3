#!/usr/bin/env python3
"""
Advanced Risk Management for Trading Bot.
Implements portfolio-level risk controls, VaR calculations, and correlation analysis.
"""

import math
import statistics
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import logging
from dataclasses import dataclass
from enum import Enum

from config import get_config
from models import Account, Trade, Position
from database import DatabaseManager
from balance_manager import BalanceHistoryManager

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class PortfolioRiskMetrics:
    """Portfolio-level risk metrics."""

    total_value: float
    total_risk: float  # Portfolio VaR
    concentration_risk: float  # Largest position % of portfolio
    correlation_risk: float  # Average correlation between positions
    liquidity_risk: float  # Based on position sizes vs average volume
    stress_test_loss: float  # Loss under stress scenario
    risk_level: RiskLevel


@dataclass
class PositionRisk:
    """Risk metrics for individual positions."""

    symbol: str
    position_size: float
    unrealized_pnl: float
    volatility: float
    beta: float  # Beta relative to portfolio
    contribution_to_risk: float
    var_95: float  # 95% VaR for position
    stop_loss_distance: float
    take_profit_distance: float


class AdvancedRiskManager:
    """
    Advanced risk management with portfolio-level controls.
    """

    def __init__(self, db: DatabaseManager, balance_manager: Optional[BalanceHistoryManager] = None):
        self.db = db
        self.balance_manager = balance_manager
        self.config = get_config()
        self.lookback_days = 30  # Days for historical analysis

        # Risk limits
        self.max_portfolio_var = 0.05  # 5% max portfolio VaR
        self.max_concentration = 0.25  # 25% max single position
        self.max_correlation = 0.8  # Max average correlation
        self.min_liquidity_ratio = 0.1  # Min position size vs avg volume
        self.max_drawdown_limit = 0.15  # 15% max drawdown before stopping

    def should_stop_trading_due_to_drawdown(self, account_id: str) -> Tuple[bool, float]:
        """
        Check if trading should stop due to excessive drawdown.

        Uses real balance history instead of simulated data.

        Args:
            account_id: Account identifier

        Returns:
            Tuple of (should_stop, current_drawdown_percentage)
        """
        if not self.balance_manager:
            logger.warning("No balance manager available for drawdown check")
            return False, 0.0

        try:
            current_drawdown = self.balance_manager.calculate_max_drawdown(account_id, days=30)

            if current_drawdown >= self.max_drawdown_limit:
                logger.warning(f"Drawdown limit exceeded: {current_drawdown:.2f}% >= {self.max_drawdown_limit:.2f}%")
                return True, current_drawdown

            return False, current_drawdown

        except Exception as e:
            logger.error(f"Error checking drawdown: {e}")
            return False, 0.0

    def calculate_portfolio_risk(self, account: Account) -> PortfolioRiskMetrics:
        """
        Calculate comprehensive portfolio risk metrics.

        Args:
            account: Current account state

        Returns:
            PortfolioRiskMetrics: Complete risk assessment
        """
        try:
            # Get current positions
            positions = account.positions

            if not positions:
                return PortfolioRiskMetrics(
                    total_value=account.balance,
                    total_risk=0.0,
                    concentration_risk=0.0,
                    correlation_risk=0.0,
                    liquidity_risk=0.0,
                    stress_test_loss=0.0,
                    risk_level=RiskLevel.LOW,
                )

            # Calculate position values and weights
            position_values = []
            position_weights = []
            total_value = account.balance

            for pos in positions:
                if pos.status == "open":
                    pos_value = abs(pos.size * pos.current_price)
                    position_values.append(pos_value)
                    position_weights.append(pos_value / total_value)
                    total_value += pos_value

            # Concentration risk (largest position %)
            concentration_risk = max(position_weights) if position_weights else 0.0

            # Portfolio VaR calculation
            portfolio_var = self._calculate_portfolio_var(positions, total_value)

            # Correlation risk
            correlation_risk = self._calculate_correlation_risk(positions)

            # Liquidity risk
            liquidity_risk = self._calculate_liquidity_risk(positions)

            # Stress test
            stress_test_loss = self._run_stress_test(positions, total_value)

            # Determine risk level
            risk_level = self._assess_risk_level(
                portfolio_var,
                concentration_risk,
                correlation_risk,
                liquidity_risk,
                stress_test_loss,
            )

            return PortfolioRiskMetrics(
                total_value=total_value,
                total_risk=portfolio_var,
                concentration_risk=concentration_risk,
                correlation_risk=correlation_risk,
                liquidity_risk=liquidity_risk,
                stress_test_loss=stress_test_loss,
                risk_level=risk_level,
            )

        except Exception as e:
            logger.error(f"Error calculating portfolio risk: {e}")
            return PortfolioRiskMetrics(
                total_value=account.balance,
                total_risk=0.0,
                concentration_risk=0.0,
                correlation_risk=0.0,
                liquidity_risk=0.0,
                stress_test_loss=0.0,
                risk_level=RiskLevel.CRITICAL,
            )

    def _calculate_portfolio_var(
        self, positions: List[Position], total_value: float
    ) -> float:
        """Calculate portfolio Value at Risk using historical simulation."""
        try:
            # Get historical returns for each position
            returns_data = {}
            symbols = [pos.asset for pos in positions if pos.status == "open"]

            for symbol in symbols:
                returns = self._get_historical_returns(symbol, self.lookback_days)
                if returns:
                    returns_data[symbol] = returns

            if not returns_data:
                return 0.0

            # Calculate portfolio returns
            portfolio_returns = []
            num_scenarios = min(1000, len(next(iter(returns_data.values()))))

            for i in range(num_scenarios):
                portfolio_return = 0.0
                for pos in positions:
                    if pos.status == "open" and pos.asset in returns_data:
                        weight = abs(pos.size * pos.current_price) / total_value
                        if i < len(returns_data[pos.asset]):
                            portfolio_return += weight * returns_data[pos.asset][i]

                portfolio_returns.append(portfolio_return)

            # Calculate 95% VaR (5th percentile)
            if portfolio_returns:
                sorted_returns = sorted(portfolio_returns)
                index = int(len(sorted_returns) * 0.05)  # 5th percentile
                var_95 = (
                    sorted_returns[index]
                    if index < len(sorted_returns)
                    else sorted_returns[0]
                )
                return abs(var_95)  # Return positive value

            return 0.0

        except Exception as e:
            logger.error(f"Error calculating portfolio VaR: {e}")
            return 0.0

    def _calculate_correlation_risk(self, positions: List[Position]) -> float:
        """Calculate average correlation between positions."""
        try:
            symbols = [pos.asset for pos in positions if pos.status == "open"]
            if len(symbols) < 2:
                return 0.0

            # Get correlation matrix
            corr_matrix = self._get_correlation_matrix(symbols, self.lookback_days)

            if corr_matrix is None:
                return 0.0

            # Calculate average correlation (excluding diagonal)
            correlations = []
            for i in range(len(symbols)):
                for j in range(i + 1, len(symbols)):
                    symbol1, symbol2 = symbols[i], symbols[j]
                    if symbol1 in corr_matrix and symbol2 in corr_matrix[symbol1]:
                        corr = corr_matrix[symbol1][symbol2]
                        if corr is not None and not math.isnan(corr):
                            correlations.append(corr)

            return statistics.mean(correlations) if correlations else 0.0

        except Exception as e:
            logger.error(f"Error calculating correlation risk: {e}")
            return 0.0

    def _calculate_liquidity_risk(self, positions: List[Position]) -> float:
        """Calculate liquidity risk based on position sizes vs average volume."""
        try:
            liquidity_risks = []

            for pos in positions:
                if pos.status == "open":
                    avg_volume = self._get_average_volume(pos.asset, self.lookback_days)
                    if avg_volume and avg_volume > 0:
                        position_volume_ratio = abs(pos.size) / avg_volume
                        # Risk increases as position size approaches average volume
                        risk = min(
                            position_volume_ratio / self.min_liquidity_ratio, 1.0
                        )
                        liquidity_risks.append(risk)

            return statistics.mean(liquidity_risks) if liquidity_risks else 0.0

        except Exception as e:
            logger.error(f"Error calculating liquidity risk: {e}")
            return 0.0

    def _run_stress_test(self, positions: List[Position], total_value: float) -> float:
        """Run stress test scenarios and return maximum loss."""
        try:
            # Define stress scenarios
            scenarios = [
                {"name": "market_crash", "returns": -0.1},  # -10% across all assets
                {"name": "high_volatility", "returns": -0.05},  # -5% scenario
                {"name": "correlated_crash", "returns": -0.15},  # -15% correlated move
            ]

            max_loss = 0.0

            for scenario in scenarios:
                scenario_loss = 0.0
                for pos in positions:
                    if pos.status == "open":
                        weight = abs(pos.size * pos.current_price) / total_value
                        scenario_loss += weight * scenario["returns"]

                max_loss = min(max_loss, scenario_loss)  # Most negative loss

            return abs(max_loss)  # Return positive loss value

        except Exception as e:
            logger.error(f"Error running stress test: {e}")
            return 0.0

    def _assess_risk_level(
        self,
        portfolio_var: float,
        concentration: float,
        correlation: float,
        liquidity: float,
        stress_loss: float,
    ) -> RiskLevel:
        """Assess overall risk level based on all metrics."""

        # Risk scoring
        risk_score = 0.0

        # VaR risk
        if portfolio_var > self.max_portfolio_var:
            risk_score += 2.0
        elif portfolio_var > self.max_portfolio_var * 0.8:
            risk_score += 1.0

        # Concentration risk
        if concentration > self.max_concentration:
            risk_score += 2.0
        elif concentration > self.max_concentration * 0.8:
            risk_score += 1.0

        # Correlation risk
        if correlation > self.max_correlation:
            risk_score += 1.5
        elif correlation > self.max_correlation * 0.8:
            risk_score += 0.5

        # Liquidity risk
        if liquidity > 1.0:
            risk_score += 1.0

        # Stress test risk
        if stress_loss > 0.1:  # >10% loss in stress scenario
            risk_score += 2.0
        elif stress_loss > 0.05:  # >5% loss
            risk_score += 1.0

        # Determine risk level
        if risk_score >= 4.0:
            return RiskLevel.CRITICAL
        elif risk_score >= 2.5:
            return RiskLevel.HIGH
        elif risk_score >= 1.0:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    def get_position_risks(self, account: Account) -> List[PositionRisk]:
        """Get detailed risk metrics for each position."""
        position_risks = []

        for pos in account.positions:
            if pos.status == "open":
                # Calculate position-specific metrics
                volatility = self._calculate_volatility(pos.asset, self.lookback_days)
                beta = self._calculate_beta(pos.asset, self.lookback_days)
                var_95 = self._calculate_position_var(pos, self.lookback_days)

                # Calculate stop loss and take profit distances
                stop_distance = (
                    abs(pos.current_price - pos.stop_loss) / pos.current_price
                    if pos.stop_loss
                    else 0.0
                )
                take_profit_distance = (
                    abs(pos.take_profit - pos.current_price) / pos.current_price
                    if pos.take_profit
                    else 0.0
                )

                position_risk = PositionRisk(
                    symbol=pos.asset,
                    position_size=abs(pos.size),
                    unrealized_pnl=pos.unrealized_pnl,
                    volatility=volatility,
                    beta=beta,
                    contribution_to_risk=var_95,  # Simplified
                    var_95=var_95,
                    stop_loss_distance=stop_distance,
                    take_profit_distance=take_profit_distance,
                )

                position_risks.append(position_risk)

        return position_risks

    def should_reject_trade(
        self, symbol: str, size: float, price: float, account: Account
    ) -> Tuple[bool, str]:
        """
        Determine if a trade should be rejected based on risk limits.

        Returns:
            Tuple[bool, str]: (should_reject, reason)
        """
        try:
            # Check portfolio risk limits
            portfolio_risk = self.calculate_portfolio_risk(account)

            # Check concentration limit
            current_exposure = sum(
                abs(pos.size * pos.current_price)
                for pos in account.positions
                if pos.status == "open"
            )
            new_exposure = current_exposure + abs(size * price)
            new_concentration = new_exposure / (account.balance + current_exposure)

            if new_concentration > self.max_concentration:
                return (
                    True,
                    f"Trade would exceed concentration limit ({new_concentration:.1%} > {self.max_concentration:.1%})",
                )

            # Check portfolio VaR limit
            if portfolio_risk.total_risk > self.max_portfolio_var:
                return (
                    True,
                    f"Portfolio VaR too high ({portfolio_risk.total_risk:.1%} > {self.max_portfolio_var:.1%})",
                )

            # Check liquidity
            avg_volume = self._get_average_volume(symbol, self.lookback_days)
            if avg_volume and abs(size) / avg_volume > self.min_liquidity_ratio:
                return True, f"Position size too large relative to average volume"

            return False, ""

        except Exception as e:
            logger.error(f"Error checking trade rejection: {e}")
            return False, ""  # Allow trade if risk check fails

    # Helper methods for data retrieval
    def _get_historical_returns(self, symbol: str, days: int) -> Optional[List[float]]:
        """Get historical daily returns for symbol."""
        try:
            # Get market data from database
            market_data = self.db.get_market_data(
                symbol, limit=days * 24
            )  # Assuming hourly data

            if len(market_data) < 2:
                return None

            # Calculate daily returns
            prices = [data["price"] for data in market_data]
            returns = []

            for i in range(1, len(prices)):
                if prices[i - 1] > 0:
                    daily_return = (prices[i] - prices[i - 1]) / prices[i - 1]
                    returns.append(daily_return)

            return returns[-days:] if len(returns) >= days else returns

        except Exception as e:
            logger.error(f"Error getting historical returns for {symbol}: {e}")
            return None

    def _get_correlation_matrix(
        self, symbols: List[str], days: int
    ) -> Optional[Dict[str, Dict[str, float]]]:
        """Calculate correlation matrix for symbols."""
        try:
            returns_dict = {}
            for symbol in symbols:
                returns = self._get_historical_returns(symbol, days)
                if returns:
                    returns_dict[symbol] = returns

            if len(returns_dict) < 2:
                return None

            # Calculate correlation matrix manually
            corr_matrix = {}
            for symbol1 in symbols:
                if symbol1 not in returns_dict:
                    continue
                corr_matrix[symbol1] = {}
                for symbol2 in symbols:
                    if symbol2 not in returns_dict:
                        continue
                    if symbol1 == symbol2:
                        corr_matrix[symbol1][symbol2] = 1.0
                    else:
                        corr = self._calculate_correlation(
                            returns_dict[symbol1], returns_dict[symbol2]
                        )
                        corr_matrix[symbol1][symbol2] = corr

            return corr_matrix

        except Exception as e:
            logger.error(f"Error calculating correlation matrix: {e}")
            return None

    def _get_average_volume(self, symbol: str, days: int) -> Optional[float]:
        """Get average daily volume for symbol."""
        try:
            market_data = self.db.get_market_data(symbol, limit=days * 24)

            if not market_data:
                return None

            volumes = [
                data.get("volume", 0) for data in market_data if data.get("volume")
            ]
            return statistics.mean(volumes) if volumes else None

        except Exception as e:
            logger.error(f"Error getting average volume for {symbol}: {e}")
            return None

    def _calculate_volatility(self, symbol: str, days: int) -> float:
        """Calculate annualized volatility."""
        try:
            returns = self._get_historical_returns(symbol, days)
            if returns and len(returns) > 1:
                return statistics.stdev(returns) * math.sqrt(252)  # Annualized
            return 0.0
        except:
            return 0.0

    def _calculate_beta(self, symbol: str, days: int) -> float:
        """Calculate beta relative to market (simplified)."""
        try:
            # For now, return 1.0 (market beta)
            # In production, would calculate against market index
            return 1.0
        except:
            return 1.0

    def _calculate_position_var(self, position: Position, days: int) -> float:
        """Calculate VaR for individual position."""
        try:
            volatility = self._calculate_volatility(position.asset, days)
            position_value = abs(position.size * position.current_price)

            # Simplified VaR calculation: position_value * volatility * z-score
            z_score_95 = 1.645  # 95% confidence
            var = position_value * volatility * z_score_95

            return var
        except:
            return 0.0

    def _calculate_correlation(
        self, returns1: List[float], returns2: List[float]
    ) -> float:
        """Calculate Pearson correlation coefficient between two return series."""
        try:
            if len(returns1) != len(returns2) or len(returns1) < 2:
                return 0.0

            # Calculate means
            mean1 = statistics.mean(returns1)
            mean2 = statistics.mean(returns2)

            # Calculate covariance and variances
            covariance = 0.0
            var1 = 0.0
            var2 = 0.0

            for r1, r2 in zip(returns1, returns2):
                diff1 = r1 - mean1
                diff2 = r2 - mean2
                covariance += diff1 * diff2
                var1 += diff1 * diff1
                var2 += diff2 * diff2

            # Avoid division by zero
            if var1 == 0 or var2 == 0:
                return 0.0

            return covariance / math.sqrt(var1 * var2)

        except Exception as e:
            logger.error(f"Error calculating correlation: {e}")
            return 0.0
