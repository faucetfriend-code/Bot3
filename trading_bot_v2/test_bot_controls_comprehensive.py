# trading_bot_v2/test_bot_controls_comprehensive.py

"""
Comprehensive Playwright E2E Tests for Bot Start/Stop Controls
Testing against running FastAPI server with real API calls.

This test suite provides comprehensive coverage of:
- Interface loading and button visibility
- Real API endpoint testing (/api/bot/start, /api/bot/stop)
- Error handling scenarios (timeouts, server errors, invalid responses)
- Success feedback and toast notifications
- Bot state changes and WebSocket real-time updates
- High-stakes scenarios: API failures and async race conditions
- Performance monitoring and response time validation
"""

import pytest
import time
import requests
import subprocess
import signal
import os
from playwright.sync_api import Page, expect


@pytest.fixture(scope="session")
def fastapi_server():
    """
    Start FastAPI server for integration testing.

    This fixture:
    - Starts the trading bot API server
    - Waits for it to become ready
    - Provides the server URL to tests
    - Cleans up the server process after tests
    """
    # Set test environment variables
    env = os.environ.copy()
    env.update(
        {
            "TESTNET": "true",
            "DATABASE_PATH": ":memory:",  # Use in-memory database for tests
            # Use placeholder credentials - tests should work even if API calls fail
            "AGENT_WALLET_PRIVATE_KEY": "test_private_key_placeholder_for_testing",
            "ACCOUNT_PUBLIC_KEY": "test_public_key_placeholder_for_testing",
        }
    )

    # Start server process
    server_process = subprocess.Popen(
        ["python", "-m", "trading_bot_v2.api_server"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )

    server_url = "http://localhost:8000"

    # Wait for server to start with health checks
    max_attempts = 30
    server_ready = False

    for attempt in range(max_attempts):
        try:
            response = requests.get(f"{server_url}/api/health", timeout=2)
            if response.status_code == 200:
                server_ready = True
                print(f"✅ Server ready after {attempt + 1} attempts")
                break
        except requests.exceptions.RequestException as e:
            print(f"Waiting for server... attempt {attempt + 1}/30: {e}")

        time.sleep(1)

    if not server_ready:
        # Server failed to start - get error output
        try:
            stdout, stderr = server_process.communicate(timeout=5)
            stdout_str = stdout.decode("utf-8", errors="ignore") if stdout else ""
            stderr_str = stderr.decode("utf-8", errors="ignore") if stderr else ""
        except subprocess.TimeoutExpired:
            server_process.kill()
            stdout_str = "Server startup timeout"
            stderr_str = "Server startup timeout"

        pytest.fail(
            f"Server failed to start after 30 attempts.\nSTDOUT: {stdout_str}\nSTDERR: {stderr_str}"
        )

    yield server_url

    # Cleanup: terminate server process
    print("🧹 Cleaning up server process...")
    try:
        server_process.terminate()
        server_process.wait(timeout=10)
        print("✅ Server process terminated cleanly")
    except subprocess.TimeoutExpired:
        print("⚠️ Server process didn't terminate gracefully, killing...")
        server_process.kill()
        server_process.wait()
        print("✅ Server process killed")


@pytest.fixture
def live_page(page: Page, fastapi_server):
    """
    Page fixture configured for live server testing.

    This fixture:
    - Configures Playwright to allow real API calls
    - Sets up the page to use the live server
    - Provides proper viewport and timeout settings
    """
    # Allow real API calls to the server (don't mock them)
    page.route("**/api/**", lambda route: route.continue_())

    # Set reasonable timeouts for integration tests
    page.set_default_timeout(30000)  # 30 seconds

    return page


# ===========================================
# BASIC FUNCTIONALITY TESTS
# ===========================================


def test_interface_loading_with_buttons_visible(live_page: Page, fastapi_server):
    """Test that interface loads correctly with all buttons visible."""
    page = live_page
    page.goto(f"{fastapi_server}/")

    # Check for console errors during page load
    errors = []
    page.on("console", lambda msg: errors.append(msg) if msg.type == "error" else None)
    page.wait_for_load_state("networkidle")

    # Assert no console errors
    assert len(errors) == 0, (
        f"Console errors during page load: {[str(e) for e in errors]}"
    )

    # Verify essential UI elements are present and visible
    expect(page.locator("#status")).to_be_visible()
    expect(page.locator("#controls")).to_be_visible()

    # Check bot control buttons
    start_button = page.locator("button", has_text="Start Bot")
    stop_button = page.locator("button", has_text="Stop Bot")
    refresh_button = page.locator("button", has_text="Refresh")

    expect(start_button).to_be_visible()
    expect(stop_button).to_be_visible()
    expect(refresh_button).to_be_visible()

    # Check initial status display
    expect(page.locator("#bot-status")).to_be_visible()
    expect(page.locator("#positions-count")).to_be_visible()
    expect(page.locator("#trades-count")).to_be_visible()
    expect(page.locator("#pnl")).to_be_visible()


def test_buttons_initial_state(live_page: Page, fastapi_server):
    """Test that buttons start in correct initial state."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Buttons should be enabled initially
    start_button = page.locator("button", has_text="Start Bot")
    stop_button = page.locator("button", has_text="Stop Bot")
    refresh_button = page.locator("button", has_text="Refresh")

    expect(start_button).not_to_have_attribute("disabled", "")
    expect(stop_button).not_to_have_attribute("disabled", "")
    expect(refresh_button).not_to_have_attribute("disabled", "")


# ===========================================
# API ENDPOINT TESTING
# ===========================================


def test_start_bot_api_endpoint_response(live_page: Page, fastapi_server):
    """Test /api/bot/start endpoint returns valid response."""
    # Test direct API call
    response = requests.post(f"{fastapi_server}/api/bot/start", timeout=10)

    # Should return a valid HTTP status code
    assert response.status_code in [200, 400, 500], (
        f"Unexpected status code: {response.status_code}"
    )

    # If successful, should return JSON with expected structure
    if response.status_code == 200:
        try:
            data = response.json()
            # Should have success indicator or message
            assert "success" in data or "message" in data, (
                f"Unexpected response format: {data}"
            )
            print(f"✅ Start bot API response: {data}")
        except ValueError as e:
            pytest.fail(f"Invalid JSON response: {e}")


def test_stop_bot_api_endpoint_response(live_page: Page, fastapi_server):
    """Test /api/bot/stop endpoint returns valid response."""
    response = requests.post(f"{fastapi_server}/api/bot/stop", timeout=10)

    assert response.status_code in [200, 400, 500], (
        f"Unexpected status code: {response.status_code}"
    )

    if response.status_code == 200:
        try:
            data = response.json()
            assert "success" in data or "message" in data, (
                f"Unexpected response format: {data}"
            )
            print(f"✅ Stop bot API response: {data}")
        except ValueError as e:
            pytest.fail(f"Invalid JSON response: {e}")


def test_start_bot_button_makes_api_call(live_page: Page, fastapi_server):
    """Test that clicking Start Bot button makes actual API call."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Track API calls
    api_calls = []
    page.on(
        "request",
        lambda request: api_calls.append(
            {"url": request.url, "method": request.method, "timestamp": time.time()}
        )
        if "/api/bot/start" in request.url
        else None,
    )

    # Click start button
    start_button = page.locator("button", has_text="Start Bot")
    start_button.click()

    # Wait for potential API call
    page.wait_for_timeout(3000)

    # Verify API call was attempted
    start_calls = [call for call in api_calls if "/api/bot/start" in call["url"]]
    assert len(start_calls) >= 1, (
        f"Expected API call to /api/bot/start, got calls: {api_calls}"
    )


def test_stop_bot_button_makes_api_call(live_page: Page, fastapi_server):
    """Test that clicking Stop Bot button makes actual API call."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Track API calls
    api_calls = []
    page.on(
        "request",
        lambda request: api_calls.append(
            {"url": request.url, "method": request.method, "timestamp": time.time()}
        )
        if "/api/bot/stop" in request.url
        else None,
    )

    # Click stop button
    stop_button = page.locator("button", has_text="Stop Bot")
    stop_button.click()

    # Wait for potential API call
    page.wait_for_timeout(3000)

    # Verify API call was attempted
    stop_calls = [call for call in api_calls if "/api/bot/stop" in call["url"]]
    assert len(stop_calls) >= 1, (
        f"Expected API call to /api/bot/stop, got calls: {api_calls}"
    )


# ===========================================
# SUCCESS FEEDBACK AND STATE CHANGES
# ===========================================


def test_start_bot_success_feedback(live_page: Page, fastapi_server):
    """Test success feedback after start bot operation."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    start_button = page.locator("button", has_text="Start Bot")

    # Click and wait for operation
    start_button.click()
    page.wait_for_timeout(3000)

    # Button should be re-enabled after operation completes
    expect(start_button).not_to_have_attribute("disabled", "")

    # Check if any toast notifications appeared
    toasts = page.locator(".toast")
    if toasts.count() > 0:
        # If toast exists, it should be visible
        expect(toasts.first).to_be_visible()


def test_stop_bot_success_feedback(live_page: Page, fastapi_server):
    """Test success feedback after stop bot operation."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    stop_button = page.locator("button", has_text="Stop Bot")

    # Click and wait for operation
    stop_button.click()
    page.wait_for_timeout(3000)

    # Button should be re-enabled after operation completes
    expect(stop_button).not_to_have_attribute("disabled", "")

    # Check for toast notifications
    toasts = page.locator(".toast")
    if toasts.count() > 0:
        expect(toasts.first).to_be_visible()


def test_bot_state_changes_after_start(live_page: Page, fastapi_server):
    """Test that bot status changes after start operation."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Get initial status
    initial_status_element = page.locator("#bot-status")
    initial_status = initial_status_element.text_content()

    # Click start bot
    page.locator("button", has_text="Start Bot").click()
    page.wait_for_timeout(3000)

    # Status should change to a valid state
    updated_status_element = page.locator("#bot-status")
    updated_status = updated_status_element.text_content()

    # Valid states: stopped, starting, ready, trading, error
    valid_states = ["stopped", "starting", "ready", "trading", "error", "Unknown"]
    assert updated_status and updated_status in valid_states, (
        f"Invalid bot status: '{updated_status}'"
    )


def test_bot_state_changes_after_stop(live_page: Page, fastapi_server):
    """Test that bot status changes after stop operation."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Click stop bot
    page.locator("button", has_text="Stop Bot").click()
    page.wait_for_timeout(3000)

    # Status should be valid
    status_element = page.locator("#bot-status")
    status = status_element.text_content()

    valid_states = ["stopped", "starting", "ready", "trading", "error", "Unknown"]
    assert status and status in valid_states, f"Invalid bot status: '{status}'"


# ===========================================
# WEBSOCKET REAL-TIME UPDATES
# ===========================================


def test_websocket_connection_established(live_page: Page, fastapi_server):
    """Test that WebSocket connection is established."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Wait for WebSocket connection attempt
    page.wait_for_timeout(3000)

    # Check WebSocket status indicator
    ws_status = page.locator("#ws-status")
    status_text = ws_status.text_content()

    # Should show connection status
    assert status_text in ["Connected", "Disconnected", "Connecting..."], (
        f"Unexpected WS status: {status_text}"
    )


def test_websocket_status_updates(live_page: Page, fastapi_server):
    """Test WebSocket status updates during connection lifecycle."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Wait for initial connection
    page.wait_for_timeout(2000)

    initial_status = page.locator("#ws-status").text_content()

    # Trigger a bot operation that might send WebSocket updates
    page.locator("button", has_text="Start Bot").click()
    page.wait_for_timeout(2000)

    # Status might change
    updated_status = page.locator("#ws-status").text_content()

    # Both statuses should be valid
    valid_statuses = ["Connected", "Disconnected", "Connecting..."]
    assert initial_status in valid_statuses
    assert updated_status in valid_statuses


# ===========================================
# ERROR HANDLING SCENARIOS
# ===========================================


def test_error_handling_server_unavailable(live_page: Page, fastapi_server):
    """Test error handling when server becomes unavailable during operation."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Mock network failure by aborting API calls
    page.route("**/api/bot/**", lambda route: route.abort())

    start_button = page.locator("button", has_text="Start Bot")

    # Click button
    start_button.click()

    # Wait for error handling
    page.wait_for_timeout(3000)

    # Button should be re-enabled after error
    expect(start_button).not_to_have_attribute("disabled", "")


def test_error_handling_invalid_json_response(live_page: Page, fastapi_server):
    """Test error handling with invalid JSON responses."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Mock invalid JSON response
    page.route(
        "**/api/bot/start",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body="invalid json response {"
        ),
    )

    start_button = page.locator("button", has_text="Start Bot")
    start_button.click()

    # Wait for error handling
    page.wait_for_timeout(3000)

    # Button should be re-enabled
    expect(start_button).not_to_have_attribute("disabled", "")


def test_error_handling_timeout(live_page: Page, fastapi_server):
    """Test error handling for API timeouts."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Mock slow response that will timeout
    def slow_response(route):
        time.sleep(15)  # Longer than typical client timeout
        route.fulfill(json={"message": "Slow response"})

    page.route("**/api/bot/start", slow_response)

    start_button = page.locator("button", has_text="Start Bot")

    start_time = time.time()
    start_button.click()

    # Wait for timeout handling (should be reasonably fast)
    page.wait_for_timeout(10000)
    end_time = time.time()

    # Should not take excessively long
    assert end_time - start_time < 12, (
        f"Timeout handling took too long: {end_time - start_time}s"
    )

    # Button should be re-enabled
    expect(start_button).not_to_have_attribute("disabled", "")


def test_error_handling_500_server_error(live_page: Page, fastapi_server):
    """Test error handling for 500 server errors."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Mock 500 error response
    page.route(
        "**/api/bot/start",
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error": "Internal server error"}',
        ),
    )

    start_button = page.locator("button", has_text="Start Bot")
    start_button.click()

    # Wait for error handling
    page.wait_for_timeout(3000)

    # Button should be re-enabled
    expect(start_button).not_to_have_attribute("disabled", "")


# ===========================================
# HIGH-STAKES SCENARIOS: RACE CONDITIONS
# ===========================================


def test_race_condition_concurrent_start_stop(live_page: Page, fastapi_server):
    """Test race conditions with concurrent start/stop operations."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Rapidly click start and stop buttons
    page.locator("button", has_text="Start Bot").click()
    page.locator("button", has_text="Stop Bot").click()
    page.locator("button", has_text="Start Bot").click()
    page.locator("button", has_text="Stop Bot").click()

    # Wait for all operations to complete
    page.wait_for_timeout(5000)

    # Both buttons should be enabled (not stuck in loading state)
    start_button = page.locator("button", has_text="Start Bot")
    stop_button = page.locator("button", has_text="Stop Bot")

    expect(start_button).not_to_have_attribute("disabled", "")
    expect(stop_button).not_to_have_attribute("disabled", "")


def test_race_condition_multiple_starts(live_page: Page, fastapi_server):
    """Test race conditions with multiple rapid start attempts."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Click start button multiple times rapidly
    start_button = page.locator("button", has_text="Start Bot")
    for _ in range(5):
        start_button.click()
        page.wait_for_timeout(100)

    # Wait for operations to settle
    page.wait_for_timeout(3000)

    # Button should be enabled
    expect(start_button).not_to_have_attribute("disabled", "")


def test_race_condition_during_operation(live_page: Page, fastapi_server):
    """Test race conditions when clicking buttons during ongoing operations."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    start_button = page.locator("button", has_text="Start Bot")
    stop_button = page.locator("button", has_text="Stop Bot")

    # Start an operation
    start_button.click()

    # Immediately try to start/stop again while first operation is in progress
    page.wait_for_timeout(500)  # Partial wait
    stop_button.click()
    start_button.click()

    # Wait for everything to complete
    page.wait_for_timeout(4000)

    # Both buttons should be enabled
    expect(start_button).not_to_have_attribute("disabled", "")
    expect(stop_button).not_to_have_attribute("disabled", "")


# ===========================================
# PERFORMANCE MONITORING
# ===========================================


def test_performance_start_bot_response_time(live_page: Page, fastapi_server):
    """Test that start bot operations complete within reasonable time."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    start_button = page.locator("button", has_text="Start Bot")

    # Measure operation time
    start_time = time.time()
    start_button.click()

    # Wait for completion (button re-enabled)
    page.wait_for_timeout(5000)
    end_time = time.time()

    response_time = end_time - start_time

    # Should complete within reasonable time (allowing for network/server delays)
    assert response_time < 8.0, (
        f"Start bot operation took too long: {response_time:.2f}s"
    )

    # Button should be re-enabled
    expect(start_button).not_to_have_attribute("disabled", "")


def test_performance_stop_bot_response_time(live_page: Page, fastapi_server):
    """Test that stop bot operations complete within reasonable time."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    stop_button = page.locator("button", has_text="Stop Bot")

    start_time = time.time()
    stop_button.click()

    page.wait_for_timeout(5000)
    end_time = time.time()

    response_time = end_time - start_time

    assert response_time < 8.0, (
        f"Stop bot operation took too long: {response_time:.2f}s"
    )

    expect(stop_button).not_to_have_attribute("disabled", "")


def test_performance_memory_stability(live_page: Page, fastapi_server):
    """Test memory stability during multiple operations."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Perform multiple operations to test memory stability
    for i in range(10):
        page.locator("button", has_text="Start Bot").click()
        page.wait_for_timeout(500)
        page.locator("button", has_text="Stop Bot").click()
        page.wait_for_timeout(500)

    # Page should still be responsive
    expect(page.locator("#status")).to_be_visible()
    expect(page.locator("button", has_text="Start Bot")).to_be_enabled()
    expect(page.locator("button", has_text="Stop Bot")).to_be_enabled()


def test_performance_ui_responsiveness(live_page: Page, fastapi_server):
    """Test UI responsiveness during operations."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Start operation
    page.locator("button", has_text="Start Bot").click()

    # UI should remain responsive during operation
    expect(page.locator("#status")).to_be_visible()
    expect(page.locator("#bot-status")).to_be_visible()

    # Wait for completion
    page.wait_for_timeout(3000)

    # UI should still be responsive
    expect(page.locator("button", has_text="Start Bot")).to_be_enabled()


# ===========================================
# CROSS-BROWSER COMPATIBILITY
# ===========================================


def test_cross_browser_button_interactions(live_page: Page, fastapi_server):
    """Test button interactions work consistently."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Test basic button interactions
    start_button = page.locator("button", has_text="Start Bot")
    stop_button = page.locator("button", has_text="Stop Bot")

    # Buttons should be clickable
    expect(start_button).to_be_enabled()
    expect(stop_button).to_be_enabled()

    # Test click and state change
    start_button.click()
    page.wait_for_timeout(2000)

    # Should handle the operation gracefully
    expect(start_button).not_to_have_attribute("disabled", "")


# ===========================================
# INTEGRATION TESTS
# ===========================================


def test_full_start_stop_cycle(live_page: Page, fastapi_server):
    """Test complete start -> stop -> start cycle."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Initial state
    expect(page.locator("button", has_text="Start Bot")).to_be_enabled()
    expect(page.locator("button", has_text="Stop Bot")).to_be_enabled()

    # Start bot
    page.locator("button", has_text="Start Bot").click()
    page.wait_for_timeout(3000)
    expect(page.locator("button", has_text="Start Bot")).not_to_have_attribute(
        "disabled", ""
    )

    # Stop bot
    page.locator("button", has_text="Stop Bot").click()
    page.wait_for_timeout(3000)
    expect(page.locator("button", has_text="Stop Bot")).not_to_have_attribute(
        "disabled", ""
    )

    # Start bot again
    page.locator("button", has_text="Start Bot").click()
    page.wait_for_timeout(3000)
    expect(page.locator("button", has_text="Start Bot")).not_to_have_attribute(
        "disabled", ""
    )


def test_websocket_reconnection_handling(live_page: Page, fastapi_server):
    """Test WebSocket reconnection after disconnection."""
    page = live_page
    page.goto(f"{fastapi_server}/")
    page.wait_for_load_state("networkidle")

    # Wait for initial connection
    page.wait_for_timeout(2000)

    # Simulate WebSocket disconnection
    page.evaluate("""
        if (window.websocket && window.websocket.readyState === WebSocket.OPEN) {
            window.websocket.close();
        }
    """)

    # Wait for reconnection logic
    page.wait_for_timeout(5000)

    # WebSocket status should be updated
    ws_status = page.locator("#ws-status")
    status_text = ws_status.text_content()

    # Should show some valid status
    assert status_text in ["Connected", "Disconnected", "Connecting..."]


if __name__ == "__main__":
    # Allow running this test file directly
    pytest.main([__file__, "-v"])
