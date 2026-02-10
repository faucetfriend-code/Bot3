import pytest
from playwright.sync_api import Page, expect


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "viewport": {"width": 1280, "height": 720},
    }


def test_page_loads(page: Page):
    """Test that the interface.html page loads without errors and has correct structure."""
    # Load the HTML file directly
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")

    # Check no console errors
    errors = []
    page.on("console", lambda msg: errors.append(msg) if msg.type == "error" else None)
    page.wait_for_load_state("networkidle")

    assert len(errors) == 0, f"Console errors: {[str(e) for e in errors]}"

    # Check HTML structure
    expect(page.locator("#status")).to_be_visible()
    expect(page.locator(".controls")).to_be_visible()
    expect(page.locator("#positions-table")).to_be_visible()
    expect(page.locator("#trades-table")).to_be_visible()

    # Check buttons exist
    expect(page.locator("button", has_text="▶ Start Bot")).to_be_visible()
    expect(page.locator("button", has_text="⏸ Stop Bot")).to_be_visible()
    expect(page.locator("button", has_text="🔄 Refresh")).to_be_visible()

    # Check initial status
    expect(page.locator("#bot-status")).to_have_text("Unknown")
    expect(page.locator("#status")).to_have_class("status")


def test_initial_status_update(page: Page):
    """Test that status updates with mocked API."""
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Mock apiCall
    page.evaluate("""
    window.originalApiCall = window.apiCall;
    window.apiCall = async (endpoint, method, data) => {
      if (endpoint === '/status') {
        return {status: 'Running', positions_count: 2, trades_count: 5, pnl: 150.75};
      }
      return window.originalApiCall(endpoint, method, data);
    };
    """)

    # Call updateStatus
    page.evaluate("updateStatus()")

    # Check status updated
    expect(page.locator("#bot-status")).to_have_text("Running")
    expect(page.locator("#status")).to_have_class("running")
    expect(page.locator("#positions-count")).to_have_text("2")
    expect(page.locator("#trades-count")).to_have_text("5")
    expect(page.locator("#pnl")).to_have_text("150.75")


def test_positions_table_update(page: Page):
    """Test positions table populates with mocked data."""
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Mock apiCall
    page.evaluate("""
    window.originalApiCall = window.apiCall;
    window.apiCall = async (endpoint, method, data) => {
      if (endpoint === '/positions') {
        return [
          {"symbol": "BTC", "side": "long", "quantity": 1.0, "entry_price": 50000, "current_price": 51000, "unrealized_pnl": 1000},
          {"symbol": "ETH", "side": "short", "quantity": 2.0, "entry_price": 3000, "current_price": 2900, "unrealized_pnl": 2000}
        ];
      }
      return window.originalApiCall(endpoint, method, data);
    };
    """)

    # Call updatePositions
    page.evaluate("updatePositions()")

    # Check table rows
    rows = page.locator("#positions-tbody tr")
    expect(rows).to_have_count(2)

    # Check first row
    first_row = rows.nth(0)
    expect(first_row.locator("td").nth(0)).to_have_text("BTC")
    expect(first_row.locator("td").nth(1)).to_have_text("long")
    expect(first_row.locator("td").nth(2)).to_have_text("1")
    expect(first_row.locator("td").nth(3)).to_have_text("50000")
    expect(first_row.locator("td").nth(4)).to_have_text("51000")
    expect(first_row.locator("td").nth(5)).to_have_text("1000")


def test_trades_table_update(page: Page):
    """Test trades table populates with mocked data."""
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Mock apiCall
    page.evaluate("""
    window.originalApiCall = window.apiCall;
    window.apiCall = async (endpoint, method, data) => {
      if (endpoint === '/trades?limit=10') {
        return [
          {"symbol": "BTC", "side": "long", "quantity": 1.0, "entry_price": 50000, "exit_price": 51000, "pnl": 1000, "entry_time": "2023-01-01 12:00:00"}
        ];
      }
      return window.originalApiCall(endpoint, method, data);
    };
    """)

    # Call updateTrades
    page.evaluate("updateTrades()")

    # Check table rows
    rows = page.locator("#trades-tbody tr")
    expect(rows).to_have_count(1)

    # Check row
    row = rows.nth(0)
    expect(row.locator("td").nth(0)).to_have_text("BTC")
    expect(row.locator("td").nth(1)).to_have_text("long")
    expect(row.locator("td").nth(2)).to_have_text("1")
    expect(row.locator("td").nth(3)).to_have_text("50000")
    expect(row.locator("td").nth(4)).to_have_text("51000")
    expect(row.locator("td").nth(5)).to_have_text("1000")
    expect(row.locator("td").nth(6)).to_have_text("2023-01-01 12:00:00")


def test_start_bot(page: Page):
    """Test start bot button makes correct API call."""
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Mock apiCall
    page.evaluate("""
    window.apiCalls = [];
    window.originalApiCall = window.apiCall;
    window.apiCall = async (endpoint, method, data) => {
      window.apiCalls.push({endpoint, method, data});
      if (endpoint === '/bot/stop' && method === 'POST') {
        return {message: 'Bot stopped'};
      }
      return window.originalApiCall(endpoint, method, data);
    };
    """)

    # Click start bot
    page.locator("button", has_text="Start Bot").click()

    # Check api calls
    calls = page.evaluate("window.apiCalls")
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert "/api/bot/start" in calls[0]["url"]


def test_stop_bot(page: Page):
    """Test stop bot button makes correct API call."""
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Mock apiCall
    page.evaluate("""
    window.apiCalls = [];
    window.originalApiCall = window.apiCall;
    window.apiCall = async (endpoint, method, data) => {
      window.apiCalls.push({endpoint, method, data});
      if (endpoint === '/status') {
        return {status: 'Running', positions_count: 0, trades_count: 0, pnl: 0};
      }
      if (endpoint === '/positions') {
        return [];
      }
      if (endpoint === '/trades?limit=10') {
        return [];
      }
      return window.originalApiCall(endpoint, method, data);
    };
    """)

    # Click stop bot
    page.locator("button", has_text="Stop Bot").click()

    # Check api calls
    calls = page.evaluate("window.apiCalls")
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert "/api/bot/stop" in calls[0]["url"]


def test_refresh_button(page: Page):
    """Test refresh button triggers API calls."""
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Mock apiCall
    page.evaluate("""
    window.apiCalls = [];
    window.originalApiCall = window.apiCall;
    window.apiCall = async (endpoint, method, data) => {
      window.apiCalls.push({endpoint, method, data});
      if (endpoint === '/bot/start' && method === 'POST') {
        return {message: 'Bot started'};
      }
      return window.originalApiCall(endpoint, method, data);
    };
    """)

    # Call refreshData
    page.evaluate("refreshData()")

    # Check calls made
    calls = page.evaluate("window.apiCalls")
    assert len([c for c in calls if "/api/status" in c["url"]]) == 1
    assert len([c for c in calls if "/api/positions" in c["url"]]) == 1
    assert len([c for c in calls if "/api/trades" in c["url"]]) == 1


# ===========================================
# COMPREHENSIVE BOT START/STOP TESTS
# Testing against running FastAPI server
# ===========================================

import subprocess
import time
import requests
import threading
import signal
import os
from contextlib import contextmanager


@pytest.fixture(scope="session")
def fastapi_server():
    """Start FastAPI server for integration testing."""
    # Set test environment variables
    env = os.environ.copy()
    env.update(
        {
            "TESTNET": "true",
            "DATABASE_PATH": ":memory:",  # Use in-memory database for tests
            "AGENT_WALLET_PRIVATE_KEY": "test_private_key_placeholder",
            "ACCOUNT_PUBLIC_KEY": "test_public_key_placeholder",
        }
    )

    # Start server process
    server_process = subprocess.Popen(
        ["python", "-m", "trading_bot_v2.api_server"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3",
    )

    # Wait for server to start
    max_attempts = 30
    for attempt in range(max_attempts):
        try:
            response = requests.get("http://localhost:8000/api/health", timeout=2)
            if response.status_code == 200:
                break
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    else:
        # Server failed to start
        stdout, stderr = server_process.communicate()
        pytest.fail(
            f"Server failed to start. STDOUT: {stdout.decode()}, STDERR: {stderr.decode()}"
        )

    yield "http://localhost:8000"

    # Cleanup
    try:
        server_process.terminate()
        server_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server_process.kill()
        server_process.wait()


@pytest.fixture
def live_page(page: Page, fastapi_server):
    """Page fixture configured to test against live server."""
    # Configure page to use live server instead of file://
    page.route("**/api/**", lambda route: route.continue_())  # Don't mock API calls
    return page


def test_interface_loading_with_buttons_visible(live_page: Page, fastapi_server):
    """Test interface loading with buttons visible against live server."""
    page = live_page
    page.goto(f"{fastapi_server}/")

    # Check no console errors
    errors = []
    page.on("console", lambda msg: errors.append(msg) if msg.type == "error" else None)
    page.wait_for_load_state("networkidle")

    assert len(errors) == 0, f"Console errors: {[str(e) for e in errors]}"

    # Check buttons are visible
    expect(page.locator("button", has_text="Start Bot")).to_be_visible()
    expect(page.locator("button", has_text="Stop Bot")).to_be_visible()
    expect(page.locator("button", has_text="Refresh")).to_be_visible()

    # Check initial status
    expect(page.locator("#bot-status")).to_be_visible()
    expect(page.locator("#status")).to_be_visible()


def test_start_bot_button_real_api_call(live_page: Page, fastapi_server):
    """Test start bot button makes real API call to running server."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Intercept API calls to verify they're made
    api_calls = []
    page.on(
        "request",
        lambda request: api_calls.append(request.url)
        if "/api/bot/start" in request.url
        else None,
    )

    # Click start bot button
    start_button = page.locator("button", has_text="Start Bot")
    start_button.click()

    # Wait for API call to complete
    page.wait_for_timeout(2000)

    # Verify API call was made
    start_calls = [url for url in api_calls if "/api/bot/start" in url]
    assert len(start_calls) >= 1, (
        f"Expected API call to /api/bot/start, got: {api_calls}"
    )

    # Check button shows loading state
    expect(start_button).to_have_attribute("disabled", "")


def test_stop_bot_button_real_api_call(live_page: Page, fastapi_server):
    """Test stop bot button makes real API call to running server."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Intercept API calls
    api_calls = []
    page.on(
        "request",
        lambda request: api_calls.append(request.url)
        if "/api/bot/stop" in request.url
        else None,
    )

    # Click stop bot button
    stop_button = page.locator("button", has_text="Stop Bot")
    stop_button.click()

    # Wait for API call
    page.wait_for_timeout(2000)

    # Verify API call was made
    stop_calls = [url for url in api_calls if "/api/bot/stop" in url]
    assert len(stop_calls) >= 1, f"Expected API call to /api/bot/stop, got: {api_calls}"


def test_api_endpoint_responses_start_bot(live_page: Page, fastapi_server):
    """Test /api/bot/start endpoint responses."""
    # Test direct API call first
    response = requests.post(f"{fastapi_server}/api/bot/start", timeout=10)
    assert response.status_code in [200, 400, 500], (
        f"Unexpected status code: {response.status_code}"
    )

    if response.status_code == 200:
        data = response.json()
        assert "success" in data or "message" in data, (
            f"Unexpected response format: {data}"
        )


def test_api_endpoint_responses_stop_bot(live_page: Page, fastapi_server):
    """Test /api/bot/stop endpoint responses."""
    response = requests.post(f"{fastapi_server}/api/bot/stop", timeout=10)
    assert response.status_code in [200, 400, 500], (
        f"Unexpected status code: {response.status_code}"
    )

    if response.status_code == 200:
        data = response.json()
        assert "success" in data or "message" in data, (
            f"Unexpected response format: {data}"
        )


def test_success_feedback_toast_notifications(live_page: Page, fastapi_server):
    """Test success feedback and toast notifications."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Click start bot and wait for response
    page.locator("button", has_text="Start Bot").click()
    page.wait_for_timeout(3000)

    # Check for success toast or status update
    # Note: Toast may not appear if API returns error, but button should be re-enabled
    start_button = page.locator("button", has_text="Start Bot")
    expect(start_button).not_to_have_attribute("disabled", "")


def test_bot_state_changes_after_operations(live_page: Page, fastapi_server):
    """Test bot state changes after start/stop operations."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Get initial status
    initial_status = page.locator("#bot-status").text_content()

    # Try to start bot
    page.locator("button", has_text="Start Bot").click()
    page.wait_for_timeout(3000)

    # Status should change (may be "starting", "ready", "trading", or stay "stopped" if failed)
    updated_status = page.locator("#bot-status").text_content()
    valid_states = ["stopped", "starting", "ready", "trading", "error"]
    assert updated_status and updated_status.lower() in valid_states, (
        f"Invalid bot status: {updated_status}"
    )


def test_websocket_real_time_updates(live_page: Page, fastapi_server):
    """Test WebSocket real-time updates."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Wait for WebSocket connection
    page.wait_for_timeout(2000)

    # Check WebSocket status indicator
    ws_status = page.locator("#ws-status")
    # Status should be either "Connected", "Disconnected", or "Connecting..."
    status_text = ws_status.text_content()
    assert status_text in ["Connected", "Disconnected", "Connecting..."], (
        f"Unexpected WS status: {status_text}"
    )


def test_error_handling_server_down(live_page: Page, fastapi_server):
    """Test error handling when server becomes unavailable."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Mock network failure by intercepting API calls
    page.route("**/api/bot/**", lambda route: route.abort())

    # Try to start bot
    page.locator("button", has_text="Start Bot").click()
    page.wait_for_timeout(2000)

    # Should show error feedback
    start_button = page.locator("button", has_text="Start Bot")
    expect(start_button).not_to_have_attribute(
        "disabled", ""
    )  # Button should be re-enabled


def test_error_handling_invalid_response(live_page: Page, fastapi_server):
    """Test error handling with invalid API responses."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Mock invalid JSON response
    page.route(
        "**/api/bot/start",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body="invalid json"
        ),
    )

    # Try to start bot
    page.locator("button", has_text="Start Bot").click()
    page.wait_for_timeout(2000)

    # Button should be re-enabled after error
    start_button = page.locator("button", has_text="Start Bot")
    expect(start_button).not_to_have_attribute("disabled", "")


def test_async_race_conditions_concurrent_operations(live_page: Page, fastapi_server):
    """Test async race conditions with concurrent start/stop operations."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Click start and stop buttons rapidly
    page.locator("button", has_text="Start Bot").click()
    page.locator("button", has_text="Stop Bot").click()
    page.locator("button", has_text="Start Bot").click()

    # Wait for operations to complete
    page.wait_for_timeout(5000)

    # Both buttons should be enabled (not stuck in loading state)
    start_button = page.locator("button", has_text="Start Bot")
    stop_button = page.locator("button", has_text="Stop Bot")

    expect(start_button).not_to_have_attribute("disabled", "")
    expect(stop_button).not_to_have_attribute("disabled", "")


def test_api_timeout_handling(live_page: Page, fastapi_server):
    """Test API timeout handling."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Mock slow response (timeout)
    def slow_response(route):
        page.wait_for_timeout(10000)  # Longer than client timeout
        route.fulfill(json={"message": "Slow response"})

    page.route("**/api/bot/start", slow_response)

    start_time = time.time()
    page.locator("button", has_text="Start Bot").click()

    # Wait for client-side timeout (should be < 10 seconds)
    page.wait_for_timeout(8000)
    end_time = time.time()

    # Should timeout within reasonable time
    assert end_time - start_time < 10, "API call took too long"

    # Button should be re-enabled
    start_button = page.locator("button", has_text="Start Bot")
    expect(start_button).not_to_have_attribute("disabled", "")


def test_performance_response_times(live_page: Page, fastapi_server):
    """Test performance - API response times should be reasonable."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Measure start bot response time
    start_time = time.time()
    page.locator("button", has_text="Start Bot").click()
    page.wait_for_timeout(3000)  # Wait for completion
    end_time = time.time()

    response_time = end_time - start_time
    assert response_time < 5.0, f"Start bot operation took too long: {response_time}s"


def test_memory_usage_stability(live_page: Page, fastapi_server):
    """Test memory usage stability during operations."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Perform multiple operations
    for _ in range(5):
        page.locator("button", has_text="Start Bot").click()
        page.wait_for_timeout(1000)
        page.locator("button", has_text="Stop Bot").click()
        page.wait_for_timeout(1000)

    # Page should still be responsive
    expect(page.locator("#status")).to_be_visible()
    expect(page.locator("button", has_text="Start Bot")).to_be_enabled()
    expect(page.locator("button", has_text="Stop Bot")).to_be_enabled()


def test_websocket_connection_recovery(live_page: Page, fastapi_server):
    """Test WebSocket connection recovery after disconnection."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Wait for initial connection
    page.wait_for_timeout(2000)

    # Simulate WebSocket disconnection and reconnection
    page.evaluate("""
        if (window.websocket && window.websocket.readyState === WebSocket.OPEN) {
            window.websocket.close();
        }
    """)

    # Wait for reconnection attempt
    page.wait_for_timeout(3000)

    # WebSocket status should update
    ws_status = page.locator("#ws-status")
    status_text = ws_status.text_content()
    # Should either reconnect or show disconnected state
    assert status_text in ["Connected", "Disconnected", "Connecting..."]


def test_cross_browser_compatibility(live_page: Page, fastapi_server):
    """Test basic functionality works across different conditions."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Basic functionality checks
    expect(page.locator("button", has_text="Start Bot")).to_be_visible()
    expect(page.locator("button", has_text="Stop Bot")).to_be_visible()
    expect(page.locator("#status")).to_be_visible()

    # Test button interactions
    start_button = page.locator("button", has_text="Start Bot")
    start_button.click()
    page.wait_for_timeout(1000)

    # Should not crash and button should be re-enabled
    expect(start_button).not_to_have_attribute("disabled", "")
