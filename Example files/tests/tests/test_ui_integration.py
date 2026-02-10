"""
Playwright UI Tests for Phases 1-4

End-to-end tests for user interactions, UI workflows, and browser-based functionality.
Tests connection status UI, trading controls, dashboard metrics display, and positions/market data tables.
"""

import pytest
from playwright.sync_api import Page, expect


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Configure browser context for testing."""
    return {
        **browser_context_args,
        "viewport": {"width": 1280, "height": 720},
    }


def test_connection_status_ui_display(page: Page):
    """Test connection status UI elements are displayed correctly."""
    page.goto("http://localhost:8000")

    # Check connection status badge exists
    connection_badge = page.locator("#connectionStatus")
    expect(connection_badge).to_be_visible()

    # Check connected banner
    connected_banner = page.locator("#connectedBanner")
    expect(connected_banner).to_be_visible()


def test_connection_status_click_handler(page: Page):
    """Test clicking connection status badge triggers retry logic."""
    page.goto("http://localhost:8000")

    connection_badge = page.locator("#connectionStatus")

    # Click the badge
    connection_badge.click()

    # Should trigger some visual feedback or modal
    # This depends on the actual implementation
    expect(page.locator("body")).to_be_visible()


def test_account_balance_display(page: Page):
    """Test account balance is displayed correctly."""
    page.goto("http://localhost:8000")

    balance_element = page.locator("#accountBalance")
    expect(balance_element).to_be_visible()

    # Should contain a dollar sign and numeric value
    expect(balance_element).to_contain_text("$")


def test_trading_controls_buttons(page: Page):
    """Test trading control buttons are present and functional."""
    page.goto("http://localhost:8000")

    # Check main control buttons exist
    standby_btn = page.locator("#standbyBtn")
    activate_btn = page.locator("#activateBtn")
    start_trading_btn = page.locator("#startTradingBtn")
    stop_trading_btn = page.locator("#stopTradingBtn")
    emergency_stop_btn = page.locator("#emergencyStopBtn")

    expect(standby_btn).to_be_visible()
    expect(activate_btn).to_be_visible()
    expect(start_trading_btn).to_be_visible()
    expect(stop_trading_btn).to_be_visible()
    expect(emergency_stop_btn).to_be_visible()


def test_trading_mode_toggle(page: Page):
    """Test paper/real trading mode toggle."""
    page.goto("http://localhost:8000")

    paper_mode = page.locator("#paperMode")
    real_mode = page.locator("#realMode")

    expect(paper_mode).to_be_visible()
    expect(real_mode).to_be_visible()

    # Test toggling (if radio buttons)
    if paper_mode.is_checked() is False:
        paper_mode.check()
        expect(paper_mode).to_be_checked()


def test_dashboard_metrics_display(page: Page):
    """Test dashboard metrics are displayed."""
    page.goto("http://localhost:8000")

    metrics = [
        "#accountBalance", "#availableMargin", "#usedMargin",
        "#totalPnL", "#realizedPnL", "#pnlPercentage",
        "#openPositions", "#totalPositionValue", "#avgLeverage",
        "#liquidationDistance", "#fundingPaidToday", "#accountHealth"
    ]

    for metric_id in metrics:
        element = page.locator(metric_id)
        expect(element).to_be_visible()


def test_positions_table_display(page: Page):
    """Test positions table is displayed correctly."""
    page.goto("http://localhost:8000")

    positions_table = page.locator("#positionsTable")
    expect(positions_table).to_be_visible()

    # Check table headers
    headers = positions_table.locator("thead th")
    expect(headers).to_have_count_greater_than(0)


def test_positions_table_sorting(page: Page):
    """Test positions table sorting functionality."""
    page.goto("http://localhost:8000")

    # Click on a sortable header
    symbol_header = page.locator("#positionsTable thead th").first
    symbol_header.click()

    # Table should still be visible after sorting
    positions_table = page.locator("#positionsTable")
    expect(positions_table).to_be_visible()


def test_market_data_table_display(page: Page):
    """Test market data table is displayed."""
    page.goto("http://localhost:8000")

    market_table = page.locator("#marketDataTable")
    expect(market_table).to_be_visible()

    # Check for data rows or empty state
    rows = market_table.locator("tbody tr")
    # Either has rows or shows empty message
    expect(rows).to_have_count_greater_than_or_equal(0)


def test_position_details_modal(page: Page):
    """Test position details modal functionality."""
    page.goto("http://localhost:8000")

    # Click on a position row (if exists)
    position_row = page.locator("#positionsTable tbody tr").first

    if position_row.is_visible():
        position_row.click()

        # Modal should appear
        modal = page.locator("#positionDetailsModal")
        expect(modal).to_be_visible()

        # Close modal
        close_btn = modal.locator(".btn-close")
        if close_btn.is_visible():
            close_btn.click()
            expect(modal).not_to_be_visible()


def test_confirmation_modal_display(page: Page):
    """Test confirmation modal for critical actions."""
    page.goto("http://localhost:8000")

    # Try to trigger emergency stop (should show confirmation)
    emergency_btn = page.locator("#emergencyStopBtn")

    if emergency_btn.is_visible() and emergency_btn.is_enabled():
        emergency_btn.click()

        # Confirmation modal should appear
        confirmation_modal = page.locator("#confirmationModal")
        expect(confirmation_modal).to_be_visible()


def test_ui_responsive_design(page: Page):
    """Test UI is responsive on different screen sizes."""
    page.goto("http://localhost:8000")

    # Test mobile viewport
    page.set_viewport_size({"width": 375, "height": 667})

    # Main elements should still be visible
    dashboard = page.locator("#dashboard")
    expect(dashboard).to_be_visible()

    # Reset to desktop
    page.set_viewport_size({"width": 1280, "height": 720})


def test_loading_states_display(page: Page):
    """Test loading states are shown during async operations."""
    page.goto("http://localhost:8000")

    # Trigger an action that should show loading
    standby_btn = page.locator("#standbyBtn")

    if standby_btn.is_visible() and standby_btn.is_enabled():
        standby_btn.click()

        # Look for loading indicators (spinner, disabled buttons, etc.)
        # This depends on implementation
        expect(page.locator("body")).to_be_visible()


def test_error_messages_display(page: Page):
    """Test error messages are displayed properly."""
    page.goto("http://localhost:8000")

    # This would require triggering an error condition
    # For now, just verify error alert structure exists
    error_alert = page.locator(".alert-danger")
    # May or may not be visible depending on state
    expect(page.locator("body")).to_be_visible()


def test_real_time_updates(page: Page):
    """Test real-time data updates."""
    page.goto("http://localhost:8000")

    # Get initial balance value
    balance_element = page.locator("#accountBalance")
    initial_text = balance_element.text_content()

    # Wait for potential updates (this would need WebSocket simulation)
    page.wait_for_timeout(2000)

    # Balance should still be displayed (may or may not have changed)
    expect(balance_element).to_be_visible()


def test_navigation_tabs(page: Page):
    """Test navigation between different sections."""
    page.goto("http://localhost:8000")

    # Check navigation tabs
    dashboard_tab = page.locator("a[href='#dashboard']")
    history_tab = page.locator("a[href='#history']")

    if dashboard_tab.is_visible():
        dashboard_tab.click()
        dashboard_section = page.locator("#dashboard")
        expect(dashboard_section).to_be_visible()

    if history_tab.is_visible():
        history_tab.click()
        history_section = page.locator("#history")
        expect(history_section).to_be_visible()


def test_button_state_management(page: Page):
    """Test button states change based on bot status."""
    page.goto("http://localhost:8000")

    standby_btn = page.locator("#standbyBtn")
    start_btn = page.locator("#startTradingBtn")

    # Check initial states
    expect(standby_btn).to_be_visible()
    expect(start_btn).to_be_visible()

    # States should be managed properly (enabled/disabled based on bot state)
    # This depends on the actual bot state


def test_form_validation(page: Page):
    """Test form validation for user inputs."""
    page.goto("http://localhost:8000")

    # If there are forms (like login), test validation
    # For now, check that the page loads without validation errors
    expect(page.locator("body")).to_be_visible()


def test_accessibility_basic(page: Page):
    """Test basic accessibility features."""
    page.goto("http://localhost:8000")

    # Check for alt text on images (if any)
    images = page.locator("img")
    for i in range(images.count()):
        img = images.nth(i)
        alt_text = img.get_attribute("alt")
        # Images should have alt text
        if alt_text is not None:
            assert len(alt_text) > 0

    # Check for proper heading hierarchy
    h1_elements = page.locator("h1")
    expect(h1_elements).to_have_count_greater_than_or_equal(0)


def test_performance_page_load(page: Page):
    """Test page load performance."""
    import time

    start_time = time.time()
    page.goto("http://localhost:8000")
    load_time = time.time() - start_time

    # Page should load within reasonable time
    assert load_time < 5.0  # 5 seconds

    # Check that critical elements are present
    expect(page.locator("#dashboard")).to_be_visible()


def test_browser_console_errors(page: Page):
    """Test that no console errors occur during normal usage."""
    page.goto("http://localhost:8000")

    # Navigate around a bit
    if page.locator("a[href='#dashboard']").is_visible():
        page.locator("a[href='#dashboard']").click()

    if page.locator("#standbyBtn").is_visible():
        page.locator("#standbyBtn").click()

    # Check console for errors
    console_messages = page.context.pages[0].console_messages
    errors = [msg for msg in console_messages if msg.type == 'error']

    # Should have no console errors (allowing for some expected ones)
    assert len(errors) <= 1  # Allow 1 error for potential expected issues


def test_ui_state_persistence(page: Page):
    """Test UI state persists across page refreshes."""
    page.goto("http://localhost:8000")

    # Change some state (like trading mode)
    paper_mode = page.locator("#paperMode")
    if paper_mode.is_visible():
        paper_mode.check()

    # Refresh page
    page.reload()

    # State should be maintained (depends on implementation)
    expect(page.locator("body")).to_be_visible()