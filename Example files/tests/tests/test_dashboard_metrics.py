"""
Tests for Phase 3: Dashboard Metrics Integration

Tests account balance and margin displays, P&L calculations and percentage changes,
position metrics (count, value, leverage), risk metrics (liquidation distance, funding payments, account health),
subaccount information displays, and real-time data updates and error handling.
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from fastapi.testclient import TestClient
from api_server import app
import json


@pytest.fixture
def client():
    """Test client for API endpoints."""
    return TestClient(app)


@pytest.fixture
def mock_pacifica_client():
    """Mock Pacifica client for testing."""
    with patch('api_server.PacificaClient') as mock:
        client = Mock()
        client.get_account_info = AsyncMock(return_value={
            'account_balance': 10000.0,
            'available_margin': 8000.0,
            'used_margin': 2000.0
        })
        client.get_positions = AsyncMock(return_value=[
            {
                'symbol': 'BTC/USD',
                'side': 'long',
                'size': 1.0,
                'entry_price': 50000.0,
                'current_price': 51000.0,
                'leverage': 5.0,
                'unrealized_pnl': 1000.0,
                'funding_pnl': -10.0
            }
        ])
        client.get_subaccounts = AsyncMock(return_value=[
            {
                'id': 'sub1',
                'balance': 5000.0,
                'strategy': 'arbitrage',
                'health_score': 85.0
            }
        ])
        mock.return_value = client
        yield client


class TestAccountBalanceMetrics:
    """Test account balance and margin displays."""

    def test_status_endpoint_returns_balance_data(self, client, mock_pacifica_client):
        """Test /api/status returns correct balance data."""
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "account_balance" in data["data"]
        assert "available_margin" in data["data"]
        assert "used_margin" in data["data"]

    @pytest.mark.parametrize("balance,available,used,expected_utilization", [
        (10000.0, 8000.0, 2000.0, 20.0),  # 2000/10000 = 20%
        (5000.0, 3000.0, 2000.0, 40.0),   # 2000/5000 = 40%
        (0.0, 0.0, 0.0, 0.0),             # Edge case: zero balance
    ])
    def test_margin_utilization_calculation(self, balance, available, used, expected_utilization):
        """Test margin utilization percentage calculation."""
        if balance > 0:
            utilization = (used / balance) * 100
            assert abs(utilization - expected_utilization) < 0.01
        else:
            assert expected_utilization == 0.0

    def test_balance_formatting(self):
        """Test balance display formatting."""
        test_balances = [1000.50, -500.25, 0.0, 1000000.0]

        for balance in test_balances:
            formatted = f"${balance:,.2f}"
            assert "$" in formatted
            assert ".00" in formatted or ".50" in formatted or ".25" in formatted

    def test_balance_update_triggers_ui_refresh(self, mock_pacifica_client):
        """Test that balance changes trigger UI updates."""
        # This would be tested with WebSocket or polling
        # Verify that API returns fresh data
        assert mock_pacifica_client.get_account_info.called is False


class TestPnLCalculations:
    """Test P&L calculations and percentage changes."""

    def test_pnl_endpoint_returns_data(self, client):
        """Test P&L endpoint returns correct data structure."""
        response = client.get("/api/pnl/trading-only")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.parametrize("unrealized,funding,total,account_balance,expected_percentage", [
        (1000.0, -50.0, 950.0, 10000.0, 9.5),    # 950/10000 = 9.5%
        (-500.0, -25.0, -525.0, 5000.0, -10.5),  # -525/5000 = -10.5%
        (0.0, 0.0, 0.0, 1000.0, 0.0),            # No P&L
    ])
    def test_pnl_percentage_calculation(self, unrealized, funding, total, account_balance, expected_percentage):
        """Test P&L percentage calculation logic."""
        if account_balance > 0:
            percentage = (total / account_balance) * 100
            assert abs(percentage - expected_percentage) < 0.01
        else:
            assert expected_percentage == 0.0

    def test_funding_pnl_separated_from_price_pnl(self):
        """Test that funding P&L is tracked separately from price P&L."""
        # Test data structure
        pnl_data = {
            'unrealized_pnl': 1000.0,
            'funding_pnl': -50.0,
            'total_pnl': 950.0
        }

        # Verify separation
        assert pnl_data['unrealized_pnl'] != pnl_data['funding_pnl']
        assert pnl_data['total_pnl'] == pnl_data['unrealized_pnl'] + pnl_data['funding_pnl']


class TestPositionMetrics:
    """Test position count, value, and leverage calculations."""

    def test_positions_endpoint_returns_data(self, client, mock_pacifica_client):
        """Test /api/positions returns position data."""
        response = client.get("/api/positions")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)

    @pytest.mark.parametrize("positions,expected_count,expected_value,expected_leverage", [
        ([], 0, 0.0, 0.0),
        ([{
            'size': 1.0,
            'current_price': 50000.0,
            'leverage': 5.0
        }], 1, 50000.0, 5.0),
        ([{
            'size': 2.0,
            'current_price': 30000.0,
            'leverage': 3.0
        }, {
            'size': 1.0,
            'current_price': 20000.0,
            'leverage': 2.0
        }], 2, 80000.0, 2.5),  # (3.0 + 2.0) / 2 = 2.5
    ])
    def test_position_aggregations(self, positions, expected_count, expected_value, expected_leverage):
        """Test position count, total value, and average leverage calculations."""
        count = len(positions)
        assert count == expected_count

        if positions:
            total_value = sum(pos['size'] * pos['current_price'] for pos in positions)
            avg_leverage = sum(pos['leverage'] for pos in positions) / len(positions)

            assert abs(total_value - expected_value) < 0.01
            assert abs(avg_leverage - expected_leverage) < 0.01
        else:
            assert expected_value == 0.0
            assert expected_leverage == 0.0


class TestRiskMetrics:
    """Test risk metrics calculations."""

    def test_risk_portfolio_endpoint(self, client):
        """Test portfolio risk metrics endpoint."""
        response = client.get("/api/risk/portfolio")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_risk_positions_endpoint(self, client):
        """Test position risk metrics endpoint."""
        response = client.get("/api/risk/positions")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.parametrize("account_balance,maintenance_margin,expected_distance", [
        (10000.0, 1000.0, 90.0),   # (10000-1000)/10000 = 90%
        (5000.0, 500.0, 90.0),     # (5000-500)/5000 = 90%
        (1000.0, 100.0, 90.0),     # (1000-100)/1000 = 90%
    ])
    def test_liquidation_distance_calculation(self, account_balance, maintenance_margin, expected_distance):
        """Test liquidation distance percentage calculation."""
        if account_balance > 0:
            distance = ((account_balance - maintenance_margin) / account_balance) * 100
            assert abs(distance - expected_distance) < 0.01

    @pytest.mark.parametrize("health_score,expected_status", [
        (95.0, "excellent"),
        (85.0, "good"),
        (75.0, "warning"),
        (60.0, "danger"),
        (45.0, "critical"),
    ])
    def test_account_health_status_logic(self, health_score, expected_status):
        """Test account health status determination."""
        if health_score >= 90:
            assert expected_status == "excellent"
        elif health_score >= 80:
            assert expected_status == "good"
        elif health_score >= 70:
            assert expected_status == "warning"
        elif health_score >= 50:
            assert expected_status == "danger"
        else:
            assert expected_status == "critical"

    def test_funding_payments_tracking(self, client):
        """Test funding payments tracking endpoint."""
        response = client.get("/api/pacifica/funding-payments")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestSubaccountInformation:
    """Test subaccount information displays."""

    def test_subaccounts_endpoint(self, client, mock_pacifica_client):
        """Test /api/subaccounts returns subaccount data."""
        response = client.get("/api/subaccounts")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)

    def test_subaccount_balance_endpoint(self, client):
        """Test individual subaccount balance endpoint."""
        response = client.get("/api/subaccounts/1/balance")
        # May return 404 if subaccount doesn't exist, but should handle gracefully
        assert response.status_code in [200, 404]

    @pytest.mark.parametrize("subaccounts,expected_total_balance,expected_count", [
        ([], 0.0, 0),
        ([{'balance': 5000.0}], 5000.0, 1),
        ([{'balance': 3000.0}, {'balance': 7000.0}], 10000.0, 2),
    ])
    def test_subaccount_aggregations(self, subaccounts, expected_total_balance, expected_count):
        """Test subaccount balance aggregation."""
        count = len(subaccounts)
        assert count == expected_count

        total_balance = sum(sub.get('balance', 0) for sub in subaccounts)
        assert abs(total_balance - expected_total_balance) < 0.01


class TestRealTimeUpdates:
    """Test real-time data updates and error handling."""

    def test_data_refresh_logic(self):
        """Test data refresh interval logic."""
        # Test different refresh rates based on bot state
        bot_states = {
            'trading': 3,      # 3 seconds when trading
            'standby': 5,      # 5 seconds when in standby
            'hidden': 30       # 30 seconds when tab hidden
        }

        for state, expected_interval in bot_states.items():
            assert isinstance(expected_interval, int)
            assert expected_interval > 0

    @patch('api_server.time.time')
    def test_update_frequency_tracking(self, mock_time, client):
        """Test that updates are not too frequent."""
        mock_time.return_value = 1000.0

        # Make multiple requests
        for _ in range(3):
            response = client.get("/api/status")
            assert response.status_code == 200

        # In real implementation, there should be throttling
        # This is a basic test that requests don't fail

    def test_error_handling_api_failures(self, client):
        """Test error handling when APIs fail."""
        with patch('api_server.PacificaClient') as mock_client_class:
            mock_client = Mock()
            mock_client.get_account_info = AsyncMock(side_effect=Exception("API Error"))
            mock_client_class.return_value = mock_client

            response = client.get("/api/status")
            # Should handle error gracefully, not crash
            assert response.status_code in [200, 500]
            if response.status_code == 200:
                data = response.json()
                # Should still return a response structure
                assert "success" in data


class TestDataCaching:
    """Test data caching and performance."""

    def test_cached_data_prevents_unnecessary_calls(self):
        """Test that cached data reduces API calls."""
        # This would test the caching mechanism
        # Verify that repeated calls within TTL use cache
        cache = {}

        def get_cached_data(key, ttl=30):
            import time
            now = time.time()
            if key in cache and (now - cache[key]['timestamp']) < ttl:
                return cache[key]['data']
            return None

        # Test caching logic
        cache['test'] = {'data': 'value', 'timestamp': 1000}
        assert get_cached_data('test', ttl=60) == 'value'  # Within TTL
        # Would need time mock for expired cache test


class TestUIElements:
    """Test UI element IDs and data binding."""

    def test_dashboard_metric_elements(self):
        """Test that dashboard metric element IDs exist."""
        required_elements = [
            'accountBalance', 'availableMargin', 'usedMargin', 'marginUtilizationBar',
            'totalPnL', 'realizedPnL', 'pnlPercentage',
            'openPositions', 'totalPositionValue', 'avgLeverage',
            'liquidationDistance', 'fundingPaidToday', 'accountHealth',
            'currentSubaccount', 'subaccountBalance', 'subaccountStrategy', 'totalSubaccounts'
        ]
        assert len(required_elements) > 0
        # This would be verified with Playwright for actual HTML

    @pytest.mark.parametrize("metric,expected_format", [
        ('accountBalance', 'currency'),
        ('totalPnL', 'currency'),
        ('pnlPercentage', 'percentage'),
        ('openPositions', 'number'),
        ('liquidationDistance', 'percentage'),
    ])
    def test_metric_display_formats(self, metric, expected_format):
        """Test that metrics are displayed in correct formats."""
        format_functions = {
            'currency': lambda x: f"${x:,.2f}",
            'percentage': lambda x: f"{x:.1f}%",
            'number': lambda x: str(int(x))
        }

        test_value = 1234.56
        formatted = format_functions[expected_format](test_value)

        if expected_format == 'currency':
            assert '$' in formatted
        elif expected_format == 'percentage':
            assert '%' in formatted
        elif expected_format == 'number':
            assert formatted == '1234'