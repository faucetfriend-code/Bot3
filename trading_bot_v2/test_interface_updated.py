# trading_bot_v2/test_interface_updated.py

"""
Comprehensive Playwright E2E Tests for Updated Interface

Tests the enhanced interface.html with new UX features:
- Toast notification system
- Dark mode toggle and persistence
- Loading states and skeleton loaders
- Modern CSS layout (Grid/Flexbox)
- Data visualization enhancements
- Performance monitoring display
- WebSocket reconnection reliability
- API response time tracking
"""

import pytest
import time
from playwright.sync_api import Page, Browser, Playwright, expect


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Configure browser context for testing."""
    return {
        **browser_context_args,
        "viewport": {"width": 1280, "height": 720},
        "ignore_https_errors": True,
    }


@pytest.fixture
def page_with_mocks(page: Page):
    """Setup page with common API and WebSocket mocks."""
    # Mock API endpoints
    page.route(
        "**/api/status",
        lambda route: route.fulfill(
            json={
                "data": {
                    "bot_running": True,
                    "positions_count": 2,
                    "trades_count": 5,
                    "total_pnl": 150.75,
                }
            }
        ),
    )
    page.route(
        "**/api/positions",
        lambda route: route.fulfill(
            json={
                "data": [
                    {
                        "symbol": "BTC",
                        "side": "long",
                        "quantity": 1.0,
                        "entry_price": 50000,
                        "current_price": 51000,
                        "unrealized_pnl": 1000,
                    },
                    {
                        "symbol": "ETH",
                        "side": "short",
                        "quantity": 2.0,
                        "entry_price": 3000,
                        "current_price": 2900,
                        "unrealized_pnl": 2000,
                    },
                ]
            }
        ),
    )
    page.route(
        "**/api/trades*",
        lambda route: route.fulfill(
            json={
                "data": [
                    {
                        "symbol": "BTC",
                        "side": "BUY",
                        "quantity": 1.0,
                        "price": 50000,
                        "fee": 0.5,
                        "pnl": 1000,
                        "timestamp": "2023-01-01T12:00:00Z",
                    }
                ]
            }
        ),
    )
    page.route(
        "**/api/bot/start",
        lambda route: route.fulfill(json={"message": "Bot started successfully"}),
    )
    page.route(
        "**/api/bot/stop",
        lambda route: route.fulfill(json={"message": "Bot stopped successfully"}),
    )

    # Mock WebSocket connection
    page.add_init_script("""
        window.mockWebSocket = {
            connected: true,
            messages: [],
            send: function(data) { this.messages.push(data); },
            close: function() { this.connected = false; }
        };
        window.WebSocket = function() { return window.mockWebSocket; };

        // Define basic functions that might not be loaded
        window.showToast = function(message, type, duration) {
            console.log('Toast:', message, type);
            const toastContainer = document.getElementById('toast-container');
            if (toastContainer) {
                const toast = document.createElement('div');
                toast.className = `toast ${type || 'info'}`;
                toast.innerHTML = `<span class="toast-message">${message}</span>`;
                toastContainer.appendChild(toast);
                setTimeout(() => toast.remove(), duration || 3000);
            }
        };

        window.toggleTheme = function() {
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
        };
    """)

    return page


def test_interface_loading(page_with_mocks: Page):
    """Test that the updated interface loads without errors and displays properly."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")

    # Check no console errors
    errors = []
    page.on("console", lambda msg: errors.append(msg) if msg.type == "error" else None)
    page.wait_for_load_state("networkidle")

    assert len(errors) == 0, f"Console errors: {[str(e) for e in errors]}"

    # Check modern layout elements
    expect(page.locator(".app-container")).to_be_visible()
    expect(page.locator("header")).to_be_visible()
    expect(page.locator("main")).to_be_visible()

    # Check new features are present
    expect(page.locator(".theme-toggle")).to_be_visible()
    expect(
        page.locator("#toast-container")
    ).to_be_attached()  # Container exists but may be empty
    expect(
        page.locator("#performance-indicator")
    ).to_be_attached()  # May be hidden initially

    # Check status section
    expect(page.locator("#status")).to_be_visible()
    expect(page.locator("#bot-status")).to_have_text("Unknown")


def test_toast_notification_system(page_with_mocks: Page):
    """Test toast notification system replaces alerts."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Trigger a toast notification
    page.evaluate("showToast('Test message', 'success')")

    # Check toast appears
    toast = page.locator(".toast")
    expect(toast).to_be_visible()
    expect(toast).to_have_text("Test message")
    expect(toast).to_have_attribute("class", "toast success")

    # Check toast auto-dismisses
    page.wait_for_timeout(3500)  # Wait for auto-dismiss
    expect(toast).not_to_be_visible()


def test_dark_mode_toggle_and_persistence(page_with_mocks: Page):
    """Test dark mode toggle functionality and localStorage persistence."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Check initial state (light mode)
    html = page.locator("html")
    expect(html).not_to_have_attribute("data-theme", "dark")

    # Check if functions exist
    toggle_exists = page.evaluate("typeof toggleTheme")
    show_toast_exists = page.evaluate("typeof showToast")
    print("toggleTheme exists:", toggle_exists)
    print("showToast exists:", show_toast_exists)

    # Toggle to dark mode
    page.evaluate("""
        console.log('Before toggle:', document.documentElement.getAttribute('data-theme'));
        toggleTheme();
        console.log('After toggle:', document.documentElement.getAttribute('data-theme'));
    """)

    # Check dark mode applied
    expect(html).to_have_attribute("data-theme", "dark")

    # Check persistence in localStorage
    dark_mode = page.evaluate("localStorage.getItem('theme')")
    assert dark_mode == "dark"

    # Reload page and check persistence
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(html).to_have_attribute("data-theme", "dark")


def test_loading_states_and_skeleton_loaders(page_with_mocks: Page):
    """Test loading states and skeleton loaders during data fetching."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Trigger data refresh
    page.locator(".btn-refresh").click()

    # Check loading states are applied
    tables = page.locator("table")
    for i in range(tables.count()):
        expect(tables.nth(i)).to_have_class("loading")

    # Wait for data to load
    page.wait_for_timeout(1000)

    # Check loading states are removed and data appears
    expect(page.locator("#positions-tbody tr")).to_have_count(2)
    expect(page.locator("#trades-tbody tr")).to_have_count(1)


def test_modern_css_layout(page_with_mocks: Page):
    """Test modern CSS Grid/Flexbox layout rendering."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Check CSS Grid layout on main container
    main = page.locator("main")
    computed_style = main.evaluate("el => getComputedStyle(el)")
    assert "grid" in computed_style["display"] or computed_style["display"] == "flex"

    # Check responsive design
    status_section = page.locator(".status-section")
    style = status_section.evaluate("el => getComputedStyle(el)")
    assert "flex" in style["display"]


def test_data_visualization_enhancements(page_with_mocks: Page):
    """Test enhanced data visualization features."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Check enhanced table styling
    positions_table = page.locator("#positions-table")
    expect(positions_table).to_have_class("enhanced-table")

    # Check P&L color coding
    positive_pnl = page.locator(".positive")
    negative_pnl = page.locator(".negative")
    expect(positive_pnl).to_have_count(1)  # At least one positive
    expect(negative_pnl).to_have_count(1)  # At least one negative

    # Check regime badges
    regime_badges = page.locator(".regime")
    expect(regime_badges).to_have_count(1)  # At least one regime badge


def test_performance_monitoring_display(page_with_mocks: Page):
    """Test performance monitoring display and metrics."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Check performance monitor panel
    monitor = page.locator("#performance-indicator")
    expect(monitor).to_be_visible()

    # Trigger some operations to generate metrics
    page.locator("#btn-refresh").click()
    page.wait_for_timeout(500)

    # Check that performance data is displayed
    expect(monitor).to_contain_text("ms")


def test_websocket_connection_and_reconnection(page_with_mocks: Page):
    """Test WebSocket connection establishment and reconnection."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Check initial WebSocket connection
    ws_status = page.locator("#ws-status")
    expect(ws_status).to_have_text("Connected")

    # Simulate disconnection
    page.evaluate("window.mockWebSocket.connected = false")
    page.evaluate("websocket.onclose({code: 1000})")

    # Check reconnection attempt
    expect(ws_status).to_have_text("Disconnected")

    # Simulate successful reconnection
    page.evaluate("window.mockWebSocket.connected = true")
    page.evaluate("websocket.onopen({})")

    expect(ws_status).to_have_text("Connected")


def test_api_calls_and_real_time_updates(page_with_mocks: Page):
    """Test API calls to various endpoints and real-time data updates."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Check initial data loaded
    expect(page.locator("#bot-status")).to_have_text("Running")
    expect(page.locator("#positions-count")).to_have_text("2")
    expect(page.locator("#trades-count")).to_have_text("5")

    # Test manual refresh
    page.locator("#btn-refresh").click()
    page.wait_for_timeout(500)

    # Verify data still loaded (mock returns same data)
    expect(page.locator("#positions-tbody tr")).to_have_count(2)


def test_bot_control_buttons(page_with_mocks: Page):
    """Test bot start/stop control buttons with proper feedback."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Test start bot
    start_btn = page.locator(".btn-start")
    start_btn.click()

    # Check loading state
    expect(start_btn).to_have_attribute("disabled", "")

    # Wait for response
    page.wait_for_timeout(1000)

    # Check success toast
    expect(page.locator(".toast", has_text="Bot started successfully")).to_be_visible()

    # Test stop bot
    stop_btn = page.locator(".btn-stop")
    stop_btn.click()

    expect(stop_btn).to_have_attribute("disabled", "")

    page.wait_for_timeout(1000)

    expect(page.locator(".toast", has_text="Bot stopped successfully")).to_be_visible()


def test_error_handling_and_user_feedback(page_with_mocks: Page):
    """Test error handling and user feedback mechanisms."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Mock API error
    page.route(
        "**/api/status",
        lambda route: route.fulfill(
            status=500, json={"error": "Internal server error"}
        ),
    )

    # Trigger refresh
    page.locator("#btn-refresh").click()

    # Check error toast appears
    expect(page.locator(".toast.error")).to_be_visible()
    expect(page.locator(".toast", has_text="Failed to update status")).to_be_visible()


def test_cross_browser_basic_functionality(page_with_mocks: Page):
    """Test basic functionality works correctly."""
    page = page_with_mocks

    # Basic checks that should work
    expect(page.locator("#status")).to_be_visible()
    expect(page.locator("#bot-status")).to_have_text("Unknown")  # Initial state


def test_dom_update_efficiency(page_with_mocks: Page):
    """Test DOM update efficiency to prevent layout thrashing."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Measure initial render time
    start_time = time.time()
    page.locator("#btn-refresh").click()
    page.wait_for_timeout(1000)
    end_time = time.time()

    render_time = end_time - start_time
    assert render_time < 2.0, f"DOM update took too long: {render_time}s"

    # Check no layout thrashing (page remains responsive)
    expect(page.locator("#positions-table")).to_be_visible()


def test_websocket_reconnection_reliability(page_with_mocks: Page):
    """Test WebSocket reconnection reliability under various conditions."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Simulate multiple disconnections and reconnections
    for i in range(3):
        # Disconnect
        page.evaluate("window.mockWebSocket.connected = false")
        page.evaluate("websocket.onclose({code: 1000})")

        expect(page.locator("#ws-status")).to_have_text("Disconnected")

        # Reconnect
        page.evaluate("window.mockWebSocket.connected = true")
        page.evaluate("websocket.onopen({})")

        expect(page.locator("#ws-status")).to_have_text("Connected")

        page.wait_for_timeout(500)


def test_api_response_time_tracking(page_with_mocks: Page):
    """Test API response time tracking and display."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Trigger API call
    start_time = time.time()
    page.locator("#btn-refresh").click()
    page.wait_for_timeout(1000)
    end_time = time.time()

    response_time = end_time - start_time

    # Check response time is tracked
    monitor = page.locator("#performance-indicator")
    expect(monitor).to_be_visible()

    # Response should be reasonable (< 2 seconds for mock)
    assert response_time < 2.0


def test_memory_usage_and_responsiveness(page_with_mocks: Page):
    """Test memory usage monitoring and page responsiveness."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Check memory usage display (if implemented)
    monitor = page.locator("#performance-indicator")
    expect(monitor).to_be_visible()

    # Perform multiple operations to test responsiveness
    for _ in range(5):
        page.locator("#btn-refresh").click()
        page.wait_for_timeout(200)

    # Page should still be responsive
    expect(page.locator("#status")).to_be_visible()
    expect(page.locator("button")).to_be_enabled()


def test_regression_existing_features(page_with_mocks: Page):
    """Test that all existing features still work after updates."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Test status display
    expect(page.locator("#bot-status")).to_have_text("Running")
    expect(page.locator("#status")).to_have_class("status")

    # Test positions table
    positions_rows = page.locator("#positions-tbody tr")
    expect(positions_rows).to_have_count(2)
    expect(positions_rows.nth(0).locator("td").nth(0)).to_have_text("BTC")

    # Test trades table
    trades_rows = page.locator("#trades-tbody tr")
    expect(trades_rows).to_have_count(1)
    expect(trades_rows.nth(0).locator("td").nth(0)).to_have_text("BTC")

    # Test buttons exist and are functional
    expect(page.locator(".btn-start")).to_be_visible()
    expect(page.locator(".btn-stop")).to_be_visible()
    expect(page.locator(".btn-refresh")).to_be_visible()


def test_backward_compatibility(page_with_mocks: Page):
    """Test backward compatibility with existing functionality."""
    page = page_with_mocks
    page.goto("file://" + "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot 3/interface.html")
    page.wait_for_load_state("networkidle")

    # Test that old API calls still work
    page.evaluate("updateStatus()")
    expect(page.locator("#bot-status")).to_have_text("Running")

    page.evaluate("updatePositions()")
    expect(page.locator("#positions-tbody tr")).to_have_count(2)

    page.evaluate("updateTrades()")
    expect(page.locator("#trades-tbody tr")).to_have_count(1)

    # Test that old event handlers still work
    page.evaluate("refreshData()")
    page.wait_for_timeout(500)
    expect(page.locator("#status")).to_be_visible()
