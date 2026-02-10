# HTML Elements Checklist for trading_bot_interface.html

## Document Structure & Metadata

- [x] `<!DOCTYPE html>` - HTML5 document type declaration
- [x] `<html lang="en">` - Root HTML element with English language attribute
- [x] `<head>` - Document head container
- [x] `<meta charset="UTF-8">` - Character encoding specification
- [x] `<meta name="viewport" content="width=device-width, initial-scale=1.0">` - Responsive viewport configuration
- [x] `<meta http-equiv="X-UA-Compatible" content="IE=edge">` - Internet Explorer compatibility mode
- [x] `<meta name="description" content="...">` - Page description for SEO
- [x] `<meta name="keywords" content="...">` - SEO keywords
- [x] `<meta name="theme-color" content="#2c3e50">` - Mobile browser theme color
- [x] `<meta name="canonical" href="http://localhost:8000/">` - Canonical URL
- [x] `<meta http-equiv="Content-Security-Policy" content="...">` - Security policy
- [x] `<meta name="referrer" content="strict-origin-when-cross-origin">` - Referrer policy
- [x] `<title>Trading Bot Control Interface</title>` - Page title
- [x] `<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,...">` - SVG favicon
- [x] `<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">` - Bootstrap CSS framework
- [x] `<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">` - Font Awesome icons
- [x] `<script src="https://cdn.jsdelivr.net/npm/chart.js" defer></script>` - Chart.js library
- [x] `<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js" defer></script>` - Lightweight Charts library
- [x] `<style>...</style>` - Embedded CSS styles

## Navigation & Layout

- [x] `<nav class="navbar navbar-expand-lg navbar-light bg-white shadow-sm">` - Main navigation bar
- [x] `<div class="container-fluid px-4">` - Bootstrap container for navbar
- [x] `<a class="navbar-brand" href="./" aria-label="Trading Bot Control - Home">` - Brand link with accessibility label
- [x] `<div class="d-flex align-items-center gap-3">` - Flex container for navbar items
- [x] `<span id="connectionStatus" class="badge bg-danger" style="cursor: pointer;">` - Connection status badge
- [x] `<small id="navbarLastUpdate" class="text-muted" style="font-size: 0.75rem;">` - Last update timestamp

## Quick Summary Bar

- [x] `<div class="quick-summary-bar" id="quickSummaryBar" style="display: none;">` - Always-visible key stats bar
- [x] `<div class="stat-item">` - Individual stat display container
- [x] `<span class="stat-label">Balance</span>` - Stat label
- [x] `<span class="stat-value" id="summaryBalance">$0.00</span>` - Stat value display

## Server Startup Section

- [x] `<div id="serverStartupSection" class="container mt-4" style="display: none;">` - Server offline message container
- [x] `<div class="card border-warning">` - Warning card for server status
- [x] `<div class="card-header bg-warning text-dark">` - Card header with warning styling
- [x] `<h5 class="card-title mb-0">` - Card title
- [x] `<i class="fas fa-robot"></i>` - Font Awesome robot icon
- [x] `<div class="card-body text-center">` - Centered card content
- [x] `<p class="card-text">` - Card text paragraph
- [x] `<button id="startAPIServerBtn" class="btn btn-success btn-lg" onclick="startAPIServer()">` - Start server button

## Authentication Forms

- [x] `<div id="apiInputContainer" class="auth-form-compact container-fluid px-4 mt-3">` - Compact authentication form
- [x] `<h6 class="mb-0"><i class="fas fa-key"></i> Connect to Pacifica.fi</h6>` - Form header
- [x] `<small class="text-muted">` - Small muted text
- [x] `<div class="form-row">` - Form row container
- [x] `<div class="form-group" style="flex: 1;">` - Form group container
- [x] `<label class="form-label small mb-1">Credentials</label>` - Form label
- [x] `<div class="alert alert-info mb-0 py-1 px-2 small" style="line-height: 1.5;">` - Info alert
- [x] `<code>.env</code>` - Inline code element
- [x] `<span id="envAccountPublicKey">Loading...</span>` - Dynamic account key display
- [x] `<div class="btn-group-auth align-self-end">` - Button group container
- [x] `<button class="btn btn-warning btn-sm" onclick="startBotStandby()" id="standbyBtnInline" title="Start bot in standby mode">` - Standby button
- [x] `<button class="btn btn-primary btn-sm" onclick="connectToPacifica()" id="connectBtn" title="Connect to Pacifica">` - Connect button

## Account Management

- [x] `<div id="accountProfilesContainer" class="container-fluid px-4 mt-2" style="display: none;">` - Account profiles container
- [x] `<label for="accountSwitcher" class="form-label">` - Label for account switcher
- [x] `<i class="fas fa-users"></i>` - Users icon
- [x] `<select id="accountSwitcher" class="form-select">` - Account selection dropdown
- [x] `<option value="">Select Account...</option>` - Default option

## Connection Status

- [x] `<div id="connectedBanner" class="container-fluid px-4 mt-3" style="display: none;">` - Connected account banner
- [x] `<div class="alert alert-success mb-0 d-flex align-items-center justify-content-between py-2">` - Success alert banner
- [x] `<span id="connectedAccountName">--</span>` - Connected account name
- [x] `<span class="badge bg-success ms-2" id="connectedWallet">--</span>` - Connected wallet badge
- [x] `<button class="btn btn-sm btn-outline-success" onclick="showAuthForm()" title="Switch Account">` - Switch account button

## Loading & Error States

- [x] `<div id="globalLoadingOverlay" class="global-loading-overlay" style="display: none;">` - Global loading overlay
- [x] `<div class="loading-content">` - Loading content container
- [x] `<div class="spinner-border text-primary" role="status">` - Bootstrap spinner
- [x] `<span class="visually-hidden">Loading...</span>` - Screen reader text
- [x] `<div class="loading-text mt-2" id="loadingText">Loading...</div>` - Loading text
- [x] `<div id="successContainer" class="success-notification"></div>` - Success notifications container
- [x] `<div id="errorContainer" class="error-notification"></div>` - Error notifications container

## Main Interface Structure

- [x] `<div class="full-interface">` - Main interface wrapper
- [x] `<div id="connectionWarning" class="alert alert-warning alert-dismissible fade show mb-4" style="display: none;">` - Connection warning alert
- [x] `<button type="button" class="btn-close" onclick="document.getElementById('connectionWarning').style.display='none';"></button>` - Dismiss button

## Navigation Tabs

- [x] `<div class="nav nav-tabs mb-4" id="mainTabs" role="tablist">` - Bootstrap tab navigation
- [x] `<button class="nav-link active" id="dashboard-tab" data-bs-toggle="tab" data-bs-target="#dashboard" type="button" role="tab">` - Dashboard tab
- [x] `<i class="fas fa-tachometer-alt"></i>` - Dashboard icon
- [x] `<button class="nav-link" id="risk-tab" data-bs-toggle="tab" data-bs-target="#risk" type="button" role="tab">` - Risk tab
- [x] `<i class="fas fa-shield-alt"></i>` - Shield icon
- [x] `<button class="nav-link" id="history-tab" data-bs-toggle="tab" data-bs-target="#history" type="button" role="tab">` - History tab
- [x] `<i class="fas fa-history"></i>` - History icon
- [x] `<button class="nav-link" id="backtest-tab" data-bs-toggle="tab" data-bs-target="#backtest" type="button" role="tab">` - Backtesting tab
- [x] `<i class="fas fa-chart-line"></i>` - Chart line icon

## Tab Content Areas

- [x] `<div class="tab-content">` - Tab content container
- [x] `<div class="tab-pane fade show active" id="dashboard" role="tabpanel">` - Dashboard tab pane

## Control Panel

- [x] `<div class="card card-priority-high mb-4">` - High priority card
- [x] `<div class="card-body control-panel-streamlined">` - Streamlined control panel
- [x] `<div class="status-group">` - Status indicators group
- [x] `<div class="status-item">` - Individual status item
- [x] `<span class="status-label">API</span>` - Status label
- [x] `<span id="serverStatusIndicator" class="status-indicator status-stopped"></span>` - Server status indicator
- [x] `<span id="serverStatusText" class="small">Disconnected</span>` - Server status text
- [x] `<span id="pacificaStatusBadge" class="badge bg-secondary status-badge">` - Pacifica status badge
- [x] `<i class="fas fa-circle-notch fa-spin"></i>` - Spinning circle icon
- [x] `<span id="botStatusIndicator" class="status-indicator status-stopped"></span>` - Bot status indicator
- [x] `<span id="botStatusText" class="small">Stopped</span>` - Bot status text
- [x] `<span id="circuitBreakerStatus" class="badge bg-success status-badge">` - Circuit breaker status
- [x] `<i class="fas fa-shield-alt"></i>` - Shield icon

## Trading Mode Toggle

- [x] `<div class="trading-mode-toggle d-flex justify-content-center" role="radiogroup">` - Trading mode toggle container
- [x] `<div id="paperMode" class="mode-option mode-active" onclick="setTradingMode('paper')" role="radio" tabindex="0">` - Paper trading mode
- [x] `<i class="fas fa-file-alt"></i>` - File icon
- [x] `<div id="realMode" class="mode-option" onclick="setTradingMode('real')" role="radio" tabindex="0">` - Real trading mode
- [x] `<i class="fas fa-dollar-sign"></i>` - Dollar sign icon

## Action Buttons

- [x] `<div class="action-group">` - Action buttons group
- [x] `<button id="standbyBtn" class="btn btn-warning btn-sm" onclick="startBotStandby()">` - Standby button
- [x] `<i class="fas fa-pause-circle"></i>` - Pause circle icon
- [x] `<button id="activateBtn" class="btn btn-success btn-sm" onclick="activateBotTrading()" disabled>` - Activate button
- [x] `<i class="fas fa-play"></i>` - Play icon
- [x] `<button id="stopBtn" class="btn btn-outline-danger btn-sm" onclick="stopBot()">` - Stop button
- [x] `<i class="fas fa-stop"></i>` - Stop icon

## Funding Alert

- [x] `<div class="row mb-3">` - Bootstrap row
- [x] `<div class="col-12">` - Full-width column
- [x] `<div id="fundingAlert" style="display: none;"></div>` - Funding alert container

## Dashboard Metrics

- [x] `<div class="row g-3 mb-4">` - Grid row with gaps
- [x] `<div class="col-lg-3 col-md-6">` - Responsive column
- [x] `<div class="metric-card-improved" style="border-left: 4px solid #667eea;">` - Improved metric card
- [x] `<div class="metric-icon text-primary"><i class="fas fa-wallet"></i></div>` - Metric icon
- [x] `<div class="metric-value text-primary" id="accountBalance">$0.00</div>` - Account balance display
- [x] `<div class="metric-label">Total Balance</div>` - Metric label
- [x] `<div class="metric-detail">` - Metric detail
- [x] `<span id="availableMargin">$0.00</span>` - Available margin
- [x] `<span id="usedMargin">$0.00</span>` - Used margin
- [x] `<div class="progress mt-2" style="height: 4px;">` - Progress bar
- [x] `<div class="progress-bar bg-primary" id="marginUtilizationBar" style="width: 0%"></div>` - Progress bar fill
- [x] `<small class="text-muted">Margin: <span id="marginUtilizationPct">0%</span></small>` - Margin utilization text

## Additional Metric Cards

- [x] `<div class="metric-card-improved" style="border-left: 4px solid #28a745;">` - P&L card
- [x] `<div class="metric-icon text-success"><i class="fas fa-chart-line"></i></div>` - Chart line icon
- [x] `<div class="metric-value" id="totalPnL">$0.00</div>` - Total P&L display
- [x] `<div class="metric-label">Total Unrealized P&L</div>` - P&L label
- [x] `<span id="realizedPnL">$0.00</span>` - Realized P&L
- [x] `<span id="pnlPercentage">0.00%</span>` - P&L percentage

- [x] `<div class="metric-card-improved" style="border-left: 4px solid #ffc107;">` - Positions card
- [x] `<div class="metric-icon text-warning"><i class="fas fa-layer-group"></i></div>` - Layer group icon
- [x] `<div class="metric-value text-warning" id="openPositions">0</div>` - Open positions count
- [x] `<div class="metric-label">Open Positions</div>` - Positions label
- [x] `<span id="totalPositionValue">$0.00</span>` - Total position value
- [x] `<span id="avgLeverage">0x</span>` - Average leverage

- [x] `<div class="metric-card-improved" style="border-left: 4px solid #dc3545;">` - Risk card
- [x] `<div class="metric-icon text-danger"><i class="fas fa-shield-alt"></i></div>` - Shield icon
- [x] `<div class="metric-value" id="liquidationDistance">--</div>` - Liquidation distance
- [x] `<div class="metric-label">Liq. Distance</div>` - Liquidation label
- [x] `<span id="fundingPaidToday">$0.00</span>` - Funding paid today
- [x] `<span id="accountHealth" class="text-success">Good</span>` - Account health

## Subaccount Info

- [x] `<div class="alert alert-light py-2 mb-4 d-flex justify-content-between align-items-center" style="border-left: 4px solid #17a2b8;">` - Subaccount alert
- [x] `<strong class="ms-2">Subaccount:</strong>` - Subaccount label
- [x] `<span id="currentSubaccount" class="badge bg-info ms-2">--</span>` - Current subaccount badge
- [x] `<span class="text-muted ms-3">Balance: <span id="subaccountBalance">$0.00</span></span>` - Subaccount balance
- [x] `<span class="text-muted ms-3">Strategy: <span id="subaccountStrategy">--</span></span>` - Subaccount strategy
- [x] `<small class="text-muted"><span id="totalSubaccounts">0</span> subaccounts</small>` - Total subaccounts count

## Collapsible Sections

- [x] `<div class="collapsible-section mb-4">` - Collapsible section container
- [x] `<div class="collapsible-header collapsed" onclick="toggleCollapsible(this)">` - Collapsible header
- [x] `<div class="d-flex align-items-center gap-3">` - Flex container for header content
- [x] `<i class="fas fa-chevron-down toggle-icon"></i>` - Chevron icon
- [x] `<div class="collapsible-content collapsed" style="padding: 15px;">` - Collapsible content

## Technical Indicators Panel

- [x] `<select class="form-select form-select-sm" id="indicatorSymbol" style="width: auto;" onclick="event.stopPropagation()">` - Symbol selector
- [x] `<option value="BTC">BTC</option>` - BTC option
- [x] `<option value="ETH">ETH</option>` - ETH option
- [x] `<select class="form-select form-select-sm" id="indicatorTimeframe" style="width: auto;" onclick="event.stopPropagation()">` - Timeframe selector
- [x] `<option value="1h">1H</option>` - 1 hour option
- [x] `<option value="4h">4H</option>` - 4 hour option
- [x] `<option value="1d">1D</option>` - 1 day option
- [x] `<i id="indicatorsSpinner" class="fas fa-spinner fa-spin" style="display: none;"></i>` - Loading spinner

## Indicators Display Grid

- [x] `<div class="row">` - Bootstrap row
- [x] `<div class="col-md-3 col-6 mb-3">` - Responsive column
- [x] `<div class="text-center p-3 bg-light rounded">` - Centered indicator card
- [x] `<h5 class="text-primary mb-1" id="rsiValue">--</h5>` - RSI value display
- [x] `<small class="text-muted">RSI (14)</small>` - RSI label
- [x] `<div class="small" id="rsiSignal">Neutral</div>` - RSI signal

## Additional Indicators

- [x] `<h5 class="text-success mb-1" id="smaValue">--</h5>` - SMA value
- [x] `<small class="text-muted">SMA/EMA (20)</small>` - SMA/EMA label
- [x] `<div class="small text-muted" id="emaValue">--</div>` - EMA value
- [x] `<h5 class="text-warning mb-1" id="macdLine">--</h5>` - MACD line
- [x] `<small class="text-muted">MACD</small>` - MACD label
- [x] `<div class="small text-muted">Signal: <span id="macdSignal">--</span></div>` - MACD signal
- [x] `<h5 class="text-info mb-1" id="atrValue">--</h5>` - ATR value
- [x] `<small class="text-muted">ATR (14)</small>` - ATR label
- [x] `<div class="small text-muted" id="lastUpdate">--</div>` - Last update

## Bollinger Bands Display

- [x] `<small class="text-muted">` - Small muted text
- [x] `<strong>Bollinger Bands:</strong>` - Bold label
- [x] `<span id="bbUpper">--</span>` - Upper band
- [x] `<span id="bbMiddle">--</span>` - Middle band
- [x] `<span id="bbLower">--</span>` - Lower band
- [x] `<span id="volumeMA">--</span>` - Volume MA
- [x] `<span id="macdHistogram">--</span>` - MACD histogram

## Price Chart

- [x] `<h6>Price Chart</h6>` - Chart title
- [x] `<canvas id="candlestickChart" width="800" height="300"></canvas>` - Candlestick chart canvas

## Funding Rates Panel

- [x] `<span class="badge bg-warning text-dark">24x/day</span>` - Warning badge
- [x] `<small class="text-light">Next: <span id="nextFundingPayment">--</span></small>` - Next payment time
- [x] `<div class="alert alert-warning py-2 mb-3" style="font-size: 0.85rem;">` - Warning alert
- [x] `<div class="table-responsive">` - Responsive table container
- [x] `<table class="table table-hover table-sm mb-0">` - Funding rates table
- [x] `<thead>` - Table header
- [x] `<tr>` - Table row
- [x] `<th>Market</th>` - Market column header
- [x] `<th>Hourly</th>` - Hourly column header
- [x] `<th>Daily</th>` - Daily column header
- [x] `<th>APR</th>` - APR column header
- [x] `<th>Trend</th>` - Trend column header
- [x] `<tbody id="fundingRatesTable">` - Funding rates table body
- [x] `<td colspan="5" class="text-center text-muted">Loading...</td>` - Loading placeholder

## Balance History Panel

- [x] `<span class="badge bg-success">Equity Tracking</span>` - Success badge
- [x] `<span id="balanceCollectorStatusBadge" class="badge badge-secondary" style="display: none;">` - Collector status badge
- [x] `<small class="text-light">Records: <span id="balanceHistoryCount">--</span></small>` - Records count
- [x] `<label for="balanceHistoryLimit" class="form-label">Records to Show:</label>` - Records limit label
- [x] `<select id="balanceHistoryLimit" class="form-select form-select-sm" onchange="loadBalanceHistory()">` - Records limit selector
- [x] `<option value="50">50 Recent</option>` - 50 records option
- [x] `<option value="100" selected>100 Recent</option>` - 100 records option
- [x] `<option value="200">200 Recent</option>` - 200 records option
- [x] `<option value="500">500 Recent</option>` - 500 records option
- [x] `<button class="btn btn-sm btn-primary me-2" onclick="loadBalanceHistory()">` - Refresh button
- [x] `<i class="fas fa-sync"></i>` - Sync icon
- [x] `<button class="btn btn-sm btn-info" onclick="exportBalanceHistory()">` - Export button
- [x] `<i class="fas fa-download"></i>` - Download icon

## Balance History Charts

- [x] `<h6>Equity Curve</h6>` - Equity curve title
- [x] `<canvas id="equityCurveChart" width="600" height="250"></canvas>` - Equity curve chart
- [x] `<h6>Summary Statistics</h6>` - Summary statistics title
- [x] `<div class="card bg-light">` - Light background card
- [x] `<div class="card-body">` - Card body
- [x] `<div class="mb-2">` - Margin bottom container
- [x] `<small class="text-muted">Current Balance:</small>` - Current balance label
- [x] `<div id="balanceCurrentBalance" class="h5 mb-0">--</div>` - Current balance value
- [x] `<small class="text-muted">Current Equity:</small>` - Current equity label
- [x] `<div id="balanceCurrentEquity" class="h5 mb-0">--</div>` - Current equity value
- [x] `<small class="text-muted">Available:</small>` - Available label
- [x] `<div id="balanceAvailable" class="h6 mb-0">--</div>` - Available value
- [x] `<small class="text-muted">Margin Used:</small>` - Margin used label
- [x] `<div id="balanceMarginUsed" class="h6 mb-0">--</div>` - Margin used value
- [x] `<hr>` - Horizontal rule
- [x] `<small class="text-muted">Max Drawdown:</small>` - Max drawdown label
- [x] `<div id="balanceMaxDrawdown" class="h6 mb-0 text-danger">--</div>` - Max drawdown value
- [x] `<small class="text-muted">Total Return:</small>` - Total return label
- [x] `<div id="balanceTotalReturn" class="h6 mb-0">--</div>` - Total return value

## Balance History Table

- [x] `<h6>Recent Balance Snapshots</h6>` - Balance snapshots title
- [x] `<div class="table-responsive" style="max-height: 300px; overflow-y: auto;">` - Scrollable table container
- [x] `<table class="table table-hover table-sm mb-0">` - Balance history table
- [x] `<thead class="table-light">` - Light table header
- [x] `<th>Date/Time</th>` - Date/Time column
- [x] `<th>Balance</th>` - Balance column
- [x] `<th>Equity</th>` - Equity column
- [x] `<th>Available</th>` - Available column
- [x] `<th>Margin Used</th>` - Margin used column
- [x] `<th>Change</th>` - Change column
- [x] `<tbody id="balanceHistoryTable">` - Balance history table body
- [x] `<tr>` - Table row
- [x] `<td colspan="6" class="text-center text-muted">Loading balance history...</td>` - Loading placeholder

## Market Data Section

- [x] `<div class="col-md-6">` - Half-width column
- [x] `<div class="card">` - Market data card
- [x] `<div class="card-header d-flex justify-content-between align-items-center">` - Card header with flex layout
- [x] `<i class="fas fa-chart-bar"></i>` - Chart bar icon
- [x] `<i id="marketDataSpinner" class="fas fa-spinner fa-spin text-muted" style="display: none;"></i>` - Market data spinner
- [x] `<table class="table table-hover">` - Market data table
- [x] `<th>Asset</th>` - Asset column header
- [x] `<th>Price</th>` - Price column header
- [x] `<th>24h Change</th>` - 24h change column header
- [x] `<th>Volume</th>` - Volume column header
- [x] `<th title="Minimum price increment">Tick Size</th>` - Tick size column with tooltip
- [x] `<th title="Minimum order size">Lot Size</th>` - Lot size column with tooltip
- [x] `<tbody id="marketDataTable">` - Market data table body
- [x] `<i class="fas fa-spinner fa-spin"></i>` - Spinner icon
- [x] `<i class="fas fa-list"></i>` - List icon
- [x] `<small id="positionsDataSource" class="text-muted">Loading...</small>` - Positions data source indicator

## Positions Table

- [x] `<table class="table table-hover">` - Positions table
- [x] `<th class="sortable" onclick="sortPositions('asset')">Asset <i class="fas fa-sort"></i></th>` - Sortable asset column
- [x] `<th class="sortable" onclick="sortPositions('side')">Side <i class="fas fa-sort"></i></th>` - Sortable side column
- [x] `<th class="sortable" onclick="sortPositions('quantity')">Size <i class="fas fa-sort"></i></th>` - Sortable size column
- [x] `<th class="sortable" onclick="sortPositions('entry_price')">Entry <i class="fas fa-sort"></i></th>` - Sortable entry column
- [x] `<th class="sortable" onclick="sortPositions('current_price')">Current <i class="fas fa-sort"></i></th>` - Sortable current column
- [x] `<th class="sortable" onclick="sortPositions('pnl')">Total P&L <i class="fas fa-sort"></i></th>` - Sortable P&L column
- [x] `<th class="sortable" onclick="sortPositions('funding_pnl')" title="Funding payments received/paid">` - Sortable funding P&L column
- [x] `<th class="sortable" onclick="sortPositions('price_pnl')" title="Price movement P&L">` - Sortable price P&L column
- [x] `<th class="sortable" onclick="sortPositions('leverage')">Lev. <i class="fas fa-sort"></i></th>` - Sortable leverage column
- [x] `<th class="sortable" onclick="sortPositions('margin_mode')">Margin <i class="fas fa-sort"></i></th>` - Sortable margin column
- [x] `<th class="sortable" onclick="sortPositions('funding_rate')" title="Current funding rate">Funding Rate <i class="fas fa-sort"></i></th>` - Sortable funding rate column
- [x] `<th>Actions</th>` - Actions column header
- [x] `<tbody id="positionsTable">` - Positions table body
- [x] `<td colspan="12" class="text-center text-muted">No open positions</td>` - No positions placeholder

## Position Details Modal

- [x] `<div class="modal fade" id="positionDetailsModal" tabindex="-1">` - Position details modal
- [x] `<div class="modal-dialog modal-lg">` - Large modal dialog
- [x] `<div class="modal-content">` - Modal content
- [x] `<div class="modal-header">` - Modal header
- [x] `<h5 class="modal-title">Position Details - <span id="modalAsset"></span></h5>` - Modal title
- [x] `<button type="button" class="btn-close" data-bs-dismiss="modal"></button>` - Modal close button
- [x] `<div class="modal-body">` - Modal body
- [x] `<div class="col-md-6">` - Half-width modal column
- [x] `<table class="table table-sm">` - Small modal table
- [x] `<tr><td><strong>Symbol:</strong></td><td id="modalSymbol"></td></tr>` - Symbol row
- [x] `<tr><td><strong>Side:</strong></td><td id="modalSide"></td></tr>` - Side row
- [x] `<tr><td><strong>Size:</strong></td><td id="modalQuantity"></td></tr>` - Size row
- [x] `<tr><td><strong>Leverage:</strong></td><td id="modalLeverage"></td></tr>` - Leverage row
- [x] `<tr><td><strong>Margin Mode:</strong></td><td id="modalMarginMode"></td></tr>` - Margin mode row
- [x] `<tr><td><strong>Opened At:</strong></td><td id="modalOpenedAt"></td></tr>` - Opened at row
- [x] `<div class="col-md-6">` - Second modal column
- [x] `<tr><td><strong>Entry Price:</strong></td><td id="modalEntryPrice"></td></tr>` - Entry price row
- [x] `<tr><td><strong>Current Price:</strong></td><td id="modalCurrentPrice"></td></tr>` - Current price row
- [x] `<tr><td><strong>Price P&L:</strong></td><td id="modalPricePnl"></td></tr>` - Price P&L row
- [x] `<tr><td><strong>Funding P&L:</strong></td><td id="modalFundingPnl"></td></tr>` - Funding P&L row
- [x] `<tr class="table-active"><td><strong>Total P&L:</strong></td><td id="modalTotalPnl"></td></tr>` - Total P&L row
- [x] `<div class="row mt-3">` - Additional modal row
- [x] `<div class="col-12">` - Full-width modal column
- [x] `<tr><td><strong>Liquidation Price:</strong></td><td id="modalLiqPrice"></td></tr>` - Liquidation price row
- [x] `<tr><td><strong>Funding Rate:</strong></td><td id="modalFundingRate"></td></tr>` - Funding rate row
- [x] `<tr><td><strong>Max Leverage:</strong></td><td id="modalMaxLeverage"></td></tr>` - Max leverage row
- [x] `<tr><td><strong>Tick Size:</strong></td><td id="modalTickSize"></td></tr>` - Tick size row
- [x] `<div class="modal-footer">` - Modal footer
- [x] `<button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>` - Close button
- [x] `<button type="button" class="btn btn-danger" onclick="closePosition(document.getElementById('modalAsset').textContent)">` - Close position button
- [x] `<i class="fas fa-times"></i>` - Times icon

## Performance Chart Section

- [x] `<div class="col-12">` - Full-width column
- [x] `<canvas id="performanceChart" width="400" height="200"></canvas>` - Performance chart canvas

## Recent Trades Section

- [x] `<table class="table table-hover">` - Trades table
- [x] `<th class="sortable" onclick="sortTrades('timestamp')">Time <i class="fas fa-sort"></i></th>` - Sortable time column
- [x] `<th class="sortable" onclick="sortTrades('asset')">Asset <i class="fas fa-sort"></i></th>` - Sortable asset column
- [x] `<th class="sortable" onclick="sortTrades('side')">Side <i class="fas fa-sort"></i></th>` - Sortable side column
- [x] `<th class="sortable" onclick="sortTrades('entry_price')">Entry Price <i class="fas fa-sort"></i></th>` - Sortable entry price column
- [x] `<th class="sortable" onclick="sortTrades('exit_price')">Exit Price <i class="fas fa-sort"></i></th>` - Sortable exit price column
- [x] `<th class="sortable" onclick="sortTrades('pnl')">P&L <i class="fas fa-sort"></i></th>` - Sortable P&L column
- [x] `<tbody id="tradesTable">` - Trades table body
- [x] `<td colspan="6" class="text-center text-muted">No recent trades</td>` - No trades placeholder

## Trading Signals Section

- [x] `<div id="signalsFeed" style="max-height: 200px; overflow-y: auto;">` - Signals feed container
- [x] `<div class="text-center text-muted">No recent signals</div>` - No signals placeholder

## Configuration Section

- [x] `<form id="configForm" role="form" aria-label="Trading bot configuration">` - Configuration form
- [x] `<div class="mb-3">` - Form group margin
- [x] `<label for="maxPositionSize" class="form-label">Max Position Size (%)</label>` - Max position size label
- [x] `<input type="number" class="form-control" id="maxPositionSize" value="10" min="1" max="100" aria-describedby="maxPositionHelp" aria-label="Maximum position size as percentage of account">` - Max position size input
- [x] `<div id="maxPositionHelp" class="form-text">Maximum size of any single position as a percentage of total account balance</div>` - Help text
- [x] `<label for="stopLossPct" class="form-label">Stop Loss (%)</label>` - Stop loss label
- [x] `<input type="number" class="form-control" id="stopLossPct" value="2.5" step="0.1" min="0.1" max="10" aria-describedby="stopLossHelp" aria-label="Stop loss percentage">` - Stop loss input
- [x] `<div id="stopLossHelp" class="form-text">Percentage loss at which positions will be automatically closed</div>` - Stop loss help text
- [x] `<label for="riskPerTrade" class="form-label">Risk per Trade (%)</label>` - Risk per trade label
- [x] `<input type="number" class="form-control" id="riskPerTrade" value="1" step="0.1" min="0.1" max="5" aria-describedby="riskPerTradeHelp" aria-label="Risk per trade as percentage">` - Risk per trade input
- [x] `<div id="riskPerTradeHelp" class="form-text">Maximum risk allowed per individual trade as a percentage of account balance</div>` - Risk per trade help text
- [x] `<button type="button" class="btn btn-primary" onclick="updateConfig()" aria-label="Save trading bot configuration changes">` - Update config button
- [x] `<i class="fas fa-save" aria-hidden="true"></i>` - Save icon

## Bot Logs Section

- [x] `<div id="logsContainer" style="max-height: 300px; overflow-y: auto; background: #f8f9fa; padding: 10px; border-radius: 5px;">` - Logs container
- [x] `<div class="text-center text-muted p-3">` - Centered muted text
- [x] `<i class="fas fa-info-circle"></i>` - Info circle icon

## Risk Tab Content

- [x] `<div class="tab-pane fade" id="risk" role="tabpanel">` - Risk tab pane
- [x] `<div class="col-md-6">` - Half-width column
- [x] `<div class="col-6">` - Half-width column
- [x] `<div class="metric-card">` - Metric card
- [x] `<div class="metric-value text-primary" id="totalValue">$0.00</div>` - Total value display
- [x] `<div class="metric-value text-warning" id="totalRisk">0.00%</div>` - Total risk display
- [x] `<div class="metric-value text-info" id="concentrationRisk">0.00%</div>` - Concentration risk display
- [x] `<div class="metric-value text-danger" id="stressTestLoss">$0.00</div>` - Stress test loss display
- [x] `<div class="d-flex align-items-center">` - Flex container
- [x] `<span class="me-2">Risk Level:</span>` - Risk level label
- [x] `<span id="riskLevel" class="badge bg-success">Low</span>` - Risk level badge
- [x] `<canvas id="riskChart" width="400" height="200"></canvas>` - Risk chart canvas

## Position Risk Analysis Table

- [x] `<table class="table table-hover">` - Risk analysis table
- [x] `<th>Asset</th>` - Asset column header
- [x] `<th>Position Size</th>` - Position size column header
- [x] `<th>Unrealized P&L</th>` - Unrealized P&L column header
- [x] `<th>Volatility</th>` - Volatility column header
- [x] `<th>Beta</th>` - Beta column header
- [x] `<th>Risk Contribution</th>` - Risk contribution column header
- [x] `<th>VaR 95%</th>` - VaR 95% column header
- [x] `<th>Stop Loss</th>` - Stop loss column header
- [x] `<th>Take Profit</th>` - Take profit column header
- [x] `<tbody id="positionRiskTable">` - Position risk table body
- [x] `<td colspan="9" class="text-center text-muted">No position risk data available</td>` - No risk data placeholder

## Strategy Configuration

- [x] `<form id="strategyConfigForm">` - Strategy config form
- [x] `<div class="row mb-4">` - Strategy row
- [x] `<div class="col-12">` - Full-width column
- [x] `<h6>Strategy Allocations</h6>` - Strategy allocations title
- [x] `<div class="row">` - Allocations row
- [x] `<div class="col-md-4">` - Quarter-width column
- [x] `<label for="trendFollowingAlloc" class="form-label">Trend Following (%)</label>` - Trend following label
- [x] `<input type="number" class="form-control" id="trendFollowingAlloc" value="75" min="0" max="100" step="5">` - Trend following input
- [x] `<label for="breakoutAlloc" class="form-label">Breakout (%)</label>` - Breakout label
- [x] `<input type="number" class="form-control" id="breakoutAlloc" value="20" min="0" max="100" step="5">` - Breakout input
- [x] `<label for="liquidationAlloc" class="form-label">Liquidation Capture (%)</label>` - Liquidation capture label
- [x] `<input type="number" class="form-control" id="liquidationAlloc" value="5" min="0" max="100" step="5">` - Liquidation capture input

## Technical Indicators Configuration

- [x] `<h6>Technical Indicators</h6>` - Technical indicators title
- [x] `<label for="rsiPeriod" class="form-label">RSI Period</label>` - RSI period label
- [x] `<input type="number" class="form-control" id="rsiPeriod" value="14" min="2" max="50">` - RSI period input
- [x] `<label for="rsiOversold" class="form-label">RSI Oversold</label>` - RSI oversold label
- [x] `<input type="number" class="form-control" id="rsiOversold" value="30" min="1" max="99">` - RSI oversold input
- [x] `<label for="rsiOverbought" class="form-label">RSI Overbought</label>` - RSI overbought label
- [x] `<input type="number" class="form-control" id="rsiOverbought" value="70" min="1" max="99">` - RSI overbought input
- [x] `<label for="atrPeriod" class="form-label">ATR Period</label>` - ATR period label
- [x] `<input type="number" class="form-control" id="atrPeriod" value="14" min="2" max="50">` - ATR period input
- [x] `<label for="smaPeriod" class="form-label">SMA Period</label>` - SMA period label
- [x] `<input type="number" class="form-control" id="smaPeriod" value="20" min="2" max="200">` - SMA period input
- [x] `<label for="emaPeriod" class="form-label">EMA Period</label>` - EMA period label
- [x] `<input type="number" class="form-control" id="emaPeriod" value="20" min="2" max="200">` - EMA period input
- [x] `<label for="bbPeriod" class="form-label">Bollinger Period</label>` - Bollinger period label
- [x] `<input type="number" class="form-control" id="bbPeriod" value="20" min="2" max="50">` - Bollinger period input
- [x] `<label for="bbStdDev" class="form-label">BB Std Dev</label>` - BB std dev label
- [x] `<input type="number" class="form-control" id="bbStdDev" value="2" min="1" max="3" step="0.1">` - BB std dev input
- [x] `<label for="macdFast" class="form-label">MACD Fast</label>` - MACD fast label
- [x] `<input type="number" class="form-control" id="macdFast" value="12" min="2" max="50">` - MACD fast input
- [x] `<label for="macdSlow" class="form-label">MACD Slow</label>` - MACD slow label
- [x] `<input type="number" class="form-control" id="macdSlow" value="26" min="2" max="50">` - MACD slow input
- [x] `<label for="macdSignal" class="form-label">MACD Signal</label>` - MACD signal label
- [x] `<input type="number" class="form-control" id="macdSignal" value="9" min="2" max="50">` - MACD signal input

## Risk Management Configuration

- [x] `<h6>Risk Management</h6>` - Risk management title
- [x] `<label for="minRRR" class="form-label">Min RRR</label>` - Min RRR label
- [x] `<input type="number" class="form-control" id="minRRR" value="2.0" min="1" max="10" step="0.1">` - Min RRR input
- [x] `<label for="targetRRR" class="form-label">Target RRR</label>` - Target RRR label
- [x] `<input type="number" class="form-control" id="targetRRR" value="3.0" min="1" max="10" step="0.1">` - Target RRR input
- [x] `<label for="maxAccountRisk" class="form-label">Max Account Risk (%)</label>` - Max account risk label
- [x] `<input type="number" class="form-control" id="maxAccountRisk" value="8" min="1" max="20" step="0.5">` - Max account risk input
- [x] `<label for="maxDrawdown" class="form-label">Max Drawdown (%)</label>` - Max drawdown label
- [x] `<input type="number" class="form-control" id="maxDrawdown" value="25" min="5" max="50" step="5">` - Max drawdown input
- [x] `<div class="d-flex gap-2">` - Button group
- [x] `<button type="button" class="btn btn-primary" onclick="loadStrategyConfig()">` - Load config button
- [x] `<i class="fas fa-download"></i>` - Download icon
- [x] `<button type="button" class="btn btn-success" onclick="saveStrategyConfig()">` - Save config button
- [x] `<i class="fas fa-save"></i>` - Save icon
- [x] `<button type="button" class="btn btn-secondary" onclick="resetStrategyConfig()">` - Reset config button
- [x] `<i class="fas fa-undo"></i>` - Undo icon

## History Tab Content

- [x] `<div class="tab-pane fade" id="history" role="tabpanel">` - History tab pane
- [x] `<div class="card mb-4">` - History filters card
- [x] `<div class="card-body">` - Card body
- [x] `<div class="row g-3" role="group" aria-label="Trade history filters">` - Filters row
- [x] `<div class="col-6 col-md-3">` - Filter column
- [x] `<label for="dateRangeFilter" class="form-label">Date Range</label>` - Date range label
- [x] `<select class="form-select" id="dateRangeFilter" aria-describedby="dateRangeHelp">` - Date range selector
- [x] `<option value="all">All Time</option>` - All time option
- [x] `<option value="today">Today</option>` - Today option
- [x] `<option value="week">This Week</option>` - This week option
- [x] `<option value="month">This Month</option>` - This month option
- [x] `<option value="custom">Custom Range</option>` - Custom range option
- [x] `<div id="dateRangeHelp" class="visually-hidden">Filter trades by time period</div>` - Hidden help text
- [x] `<div class="col-6 col-md-2">` - Asset filter column
- [x] `<label for="assetFilter" class="form-label">Asset</label>` - Asset filter label
- [x] `<select class="form-select" id="assetFilter" aria-describedby="assetFilterHelp">` - Asset filter selector
- [x] `<option value="">All Assets</option>` - All assets option
- [x] `<option value="BTC">BTC</option>` - BTC option
- [x] `<option value="ETH">ETH</option>` - ETH option
- [x] `<option value="SOL">SOL</option>` - SOL option
- [x] `<div id="assetFilterHelp" class="visually-hidden">Filter trades by cryptocurrency asset</div>` - Hidden asset help
- [x] `<div class="col-6 col-md-2">` - Side filter column
- [x] `<label for="sideFilter" class="form-label">Side</label>` - Side filter label
- [x] `<select class="form-select" id="sideFilter" aria-describedby="sideFilterHelp">` - Side filter selector
- [x] `<option value="">All Sides</option>` - All sides option
- [x] `<option value="long">Long</option>` - Long option
- [x] `<option value="short">Short</option>` - Short option
- [x] `<div id="sideFilterHelp" class="visually-hidden">Filter trades by position side (buy or sell)</div>` - Hidden side help
- [x] `<div class="col-6 col-md-2">` - Min P&L filter column
- [x] `<label for="minPnLFilter" class="form-label">Min P&L</label>` - Min P&L label
- [x] `<input type="number" class="form-control" id="minPnLFilter" placeholder="0.00" step="0.01" aria-describedby="minPnLHelp" aria-label="Minimum profit and loss filter">` - Min P&L input
- [x] `<div id="minPnLHelp" class="visually-hidden">Show only trades with profit/loss above this amount</div>` - Hidden P&L help
- [x] `<div class="col-6 col-md-3">` - Export format column
- [x] `<label for="exportFormat" class="form-label">Export Format</label>` - Export format label
- [x] `<select class="form-select" id="exportFormat" aria-describedby="exportFormatHelp">` - Export format selector
- [x] `<option value="csv">CSV</option>` - CSV option
- [x] `<option value="json">JSON</option>` - JSON option
- [x] `<div id="exportFormatHelp" class="visually-hidden">Choose file format for trade history export</div>` - Hidden export help
- [x] `<div class="col-12 col-md-3">` - Action buttons column
- [x] `<label class="form-label">&nbsp;</label>` - Spacer label
- [x] `<div class="d-flex flex-wrap gap-2" role="group" aria-label="Filter actions">` - Action buttons container
- [x] `<button class="btn btn-primary btn-sm" onclick="applyFilters()" aria-label="Apply selected filters to trade history">` - Apply filters button
- [x] `<i class="fas fa-search" aria-hidden="true"></i>` - Search icon
- [x] `<span class="d-none d-sm-inline">Apply Filters</span>` - Apply filters text
- [x] `<button class="btn btn-outline-secondary btn-sm" onclick="clearFilters()" aria-label="Clear all applied filters">` - Clear filters button
- [x] `<i class="fas fa-times"></i>` - Times icon
- [x] `<span class="d-none d-sm-inline">Clear</span>` - Clear text
- [x] `<button class="btn btn-outline-success btn-sm" onclick="exportTrades()" aria-label="Export filtered trade history" aria-describedby="exportTradesHelp">` - Export trades button
- [x] `<i class="fas fa-download"></i>` - Download icon
- [x] `<span class="d-none d-sm-inline">Export</span>` - Export text
- [x] `<div id="exportTradesHelp" class="visually-hidden">Download trade history in selected format</div>` - Hidden export help

## Trade History Table

- [x] `<div class="card">` - Trade history card
- [x] `<div class="card-header d-flex justify-content-between align-items-center">` - Card header
- [x] `<small class="text-muted" id="tradeCount">0 trades</small>` - Trade count display
- [x] `<table class="table table-hover" id="tradeHistoryTable">` - Trade history table
- [x] `<th>Time</th>` - Time column header
- [x] `<th>Asset</th>` - Asset column header
- [x] `<th>Side</th>` - Side column header
- [x] `<th>Strategy</th>` - Strategy column header
- [x] `<th>Entry Price</th>` - Entry price column header
- [x] `<th>Exit Price</th>` - Exit price column header
- [x] `<th>Quantity</th>` - Quantity column header
- [x] `<th>P&L</th>` - P&L column header
- [x] `<th>Duration</th>` - Duration column header
- [x] `<tbody id="tradeHistoryTableBody">` - Trade history table body
- [x] `<td colspan="9" class="text-center text-muted">No trade history available</td>` - No history placeholder

## Pagination Controls

- [x] `<nav aria-label="Trade history pagination" id="tradeHistoryPagination" style="display: none;">` - Pagination nav
- [x] `<ul class="pagination justify-content-center mt-3">` - Pagination list
- [x] `<li class="page-item" id="prevPageBtn">` - Previous page item
- [x] `<a class="page-link" href="#" onclick="changeTradeHistoryPage(paginationSettings.history.page - 1)" aria-label="Previous page">` - Previous page link
- [x] `<span aria-hidden="true">&laquo;</span>` - Left arrow
- [x] `<li class="page-item disabled">` - Disabled page item
- [x] `<span class="page-link" id="pageInfo">Page 1 of 1</span>` - Page info
- [x] `<li class="page-item" id="nextPageBtn">` - Next page item
- [x] `<a class="page-link" href="#" onclick="changeTradeHistoryPage(paginationSettings.history.page + 1)" aria-label="Next page">` - Next page link
- [x] `<span aria-hidden="true">&raquo;</span>` - Right arrow

## Trade Statistics

- [x] `<div class="col-md-3">` - Stat column
- [x] `<div class="card text-center">` - Stat card
- [x] `<h5 class="card-title text-primary" id="totalTradesStat">0</h5>` - Total trades stat
- [x] `<p class="card-text">Total Trades</p>` - Total trades label
- [x] `<h5 class="card-title text-success" id="winningTradesStat">0</h5>` - Winning trades stat
- [x] `<p class="card-text">Winning Trades</p>` - Winning trades label
- [x] `<h5 class="card-title text-info" id="winRateStat">0.0%</h5>` - Win rate stat
- [x] `<p class="card-text">Win Rate</p>` - Win rate label
- [x] `<h5 class="card-title text-warning" id="avgPnLStat">$0.00</h5>` - Avg P&L stat
- [x] `<p class="card-text">Avg P&L per Trade</p>` - Avg P&L label

## Confirmation Modal

- [x] `<div class="modal fade" id="confirmationModal" tabindex="-1">` - Confirmation modal
- [x] `<div class="modal-dialog">` - Modal dialog
- [x] `<div class="modal-content confirmation-modal">` - Modal content
- [x] `<h5 class="modal-title" id="confirmationTitle">Confirm Action</h5>` - Modal title
- [x] `<div class="modal-body" id="confirmationMessage">` - Modal body
- [x] `<button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>` - Cancel button
- [x] `<button type="button" class="btn btn-primary" id="confirmBtn">Confirm</button>` - Confirm button

## Backtesting Tab Content

- [x] `<div class="tab-pane fade" id="backtest" role="tabpanel">` - Backtesting tab pane
- [x] `<div class="card">` - Backtesting config card
- [x] `<div class="card-header">` - Card header
- [x] `<h5 class="mb-0"><i class="fas fa-chart-line"></i> Backtesting Configuration</h5>` - Card title
- [x] `<form id="backtestForm">` - Backtest form
- [x] `<div class="col-md-6">` - Symbol column
- [x] `<div class="mb-3">` - Form group
- [x] `<label for="backtestSymbol" class="form-label">Symbol</label>` - Symbol label
- [x] `<input type="text" class="form-control" id="backtestSymbol" value="BTC" required>` - Symbol input
- [x] `<div class="col-md-3">` - Start date column
- [x] `<label for="backtestStartDate" class="form-label">Start Date</label>` - Start date label
- [x] `<input type="date" class="form-control" id="backtestStartDate" required>` - Start date input
- [x] `<div class="col-md-3">` - End date column
- [x] `<label for="backtestEndDate" class="form-label">End Date</label>` - End date label
- [x] `<input type="date" class="form-control" id="backtestEndDate" required>` - End date input
- [x] `<div class="col-md-3">` - Initial balance column
- [x] `<label for="backtestInitialBalance" class="form-label">Initial Balance ($)</label>` - Initial balance label
- [x] `<input type="number" class="form-control" id="backtestInitialBalance" value="10000" min="1" required>` - Initial balance input
- [x] `<div class="col-md-3">` - Commission column
- [x] `<label for="backtestCommission" class="form-label">Commission per Trade</label>` - Commission label
- [x] `<input type="number" class="form-control" id="backtestCommission" value="0.001" step="0.001" min="0" required>` - Commission input
- [x] `<div class="col-md-3">` - Slippage column
- [x] `<label for="backtestSlippage" class="form-label">Slippage Amount</label>` - Slippage label
- [x] `<input type="number" class="form-control" id="backtestSlippage" value="0.0005" step="0.0001" min="0" required>` - Slippage input
- [x] `<div class="col-md-3">` - Data source column
- [x] `<label for="backtestDataSource" class="form-label">Data Source</label>` - Data source label
- [x] `<select class="form-control" id="backtestDataSource">` - Data source selector
- [x] `<option value="database">Database</option>` - Database option
- [x] `<option value="csv">CSV File</option>` - CSV option
- [x] `<div class="col-md-6">` - CSV file column
- [x] `<div class="mb-3" id="csvFileSection" style="display: none;">` - CSV file section
- [x] `<label for="csvFileInput" class="form-label">CSV File</label>` - CSV file label
- [x] `<input type="file" class="form-control" id="csvFileInput" accept=".csv">` - CSV file input
- [x] `<div class="form-text">Upload a CSV file with OHLCV data for backtesting</div>` - CSV help text
- [x] `<div class="col-md-6">` - Options column
- [x] `<div class="form-check">` - Risk management checkbox
- [x] `<input class="form-check-input" type="checkbox" id="backtestRiskManagement" checked>` - Risk management input
- [x] `<label class="form-check-label" for="backtestRiskManagement">` - Risk management label
- [x] `<div class="form-check">` - Fractional shares checkbox
- [x] `<input class="form-check-input" type="checkbox" id="backtestFractionalShares" checked>` - Fractional shares input
- [x] `<label class="form-check-label" for="backtestFractionalShares">` - Fractional shares label
- [x] `<button type="button" class="btn btn-primary" onclick="runBacktest()">` - Run backtest button
- [x] `<i class="fas fa-play"></i>` - Play icon
- [x] `<button type="button" class="btn btn-secondary" onclick="resetBacktestForm()">` - Reset form button
- [x] `<i class="fas fa-undo"></i>` - Undo icon

## Backtesting Status Section

- [x] `<div class="row mt-4" id="backtestStatusSection" style="display: none;">` - Backtest status section
- [x] `<div class="col-12">` - Full-width column
- [x] `<div class="card">` - Status card
- [x] `<div class="card-header">` - Card header
- [x] `<h6 class="mb-0"><i class="fas fa-spinner fa-spin"></i> Backtest Status</h6>` - Status title
- [x] `<div class="col-md-3">` - Status column
- [x] `<div class="text-center">` - Centered content
- [x] `<div class="mb-2">` - Margin container
- [x] `<div class="spinner-border text-primary" role="status" id="backtestSpinner" style="display: none;">` - Status spinner
- [x] `<span class="visually-hidden">Running...</span>` - Screen reader text
- [x] `<small class="text-muted d-block">Status</small>` - Status label
- [x] `<span id="backtestStatusText">Idle</span>` - Status text
- [x] `<div class="col-md-3">` - Progress column
- [x] `<small class="text-muted d-block">Progress</small>` - Progress label
- [x] `<div class="progress mt-2" style="height: 20px;">` - Progress bar container
- [x] `<div class="progress-bar" id="backtestProgressBar" role="progressbar" style="width: 0%"></div>` - Progress bar
- [x] `<span id="backtestProgressText">0%</span>` - Progress text
- [x] `<div class="col-md-6">` - Message column
- [x] `<small class="text-muted d-block">Message</small>` - Message label
- [x] `<span id="backtestMessage">Ready to start backtest</span>` - Message text

## Backtesting Results Section

- [x] `<div class="row mt-4" id="backtestResultsSection" style="display: none;">` - Results section
- [x] `<div class="col-12">` - Full-width column
- [x] `<div class="card">` - Results card
- [x] `<div class="card-header d-flex justify-content-between align-items-center">` - Results header
- [x] `<h6 class="mb-0"><i class="fas fa-chart-bar"></i> Backtest Results</h6>` - Results title
- [x] `<button class="btn btn-outline-success btn-sm" onclick="exportBacktestResults()">` - Export results button
- [x] `<i class="fas fa-download"></i>` - Download icon
- [x] `<div class="row mb-4">` - Metrics row
- [x] `<div class="col-md-3">` - Metric column
- [x] `<div class="card text-center border-primary">` - Primary metric card
- [x] `<div class="card-body">` - Card body
- [x] `<h5 class="card-title text-primary" id="backtestTotalReturn">0.00%</h5>` - Total return display
- [x] `<p class="card-text">Total Return</p>` - Total return label
- [x] `<div class="card text-center border-success">` - Success metric card
- [x] `<h5 class="card-title text-success" id="backtestWinRate">0.00%</h5>` - Win rate display
- [x] `<p class="card-text">Win Rate</p>` - Win rate label
- [x] `<div class="card text-center border-info">` - Info metric card
- [x] `<h5 class="card-title text-info" id="backtestMaxDrawdown">0.00%</h5>` - Max drawdown display
- [x] `<p class="card-text">Max Drawdown</p>` - Max drawdown label
- [x] `<div class="card text-center border-warning">` - Warning metric card
- [x] `<h5 class="card-title text-warning" id="backtestSharpeRatio">0.00</h5>` - Sharpe ratio display
- [x] `<p class="card-text">Sharpe Ratio</p>` - Sharpe ratio label
- [x] `<div class="row">` - Equity curve row
- [x] `<div class="col-12">` - Full-width column
- [x] `<div class="card">` - Equity curve card
- [x] `<div class="card-header">` - Card header
- [x] `<h6 class="mb-0">Equity Curve</h6>` - Equity curve title
- [x] `<canvas id="equityCurveChart" width="400" height="200"></canvas>` - Equity curve canvas
- [x] `<div class="row mt-4">` - Trades row
- [x] `<div class="card">` - Trades card
- [x] `<div class="card-header">` - Card header
- [x] `<h6 class="mb-0">Trade History</h6>` - Trade history title
- [x] `<table class="table table-striped" id="backtestTradesTable">` - Backtest trades table
- [x] `<th>Entry Time</th>` - Entry time column
- [x] `<th>Symbol</th>` - Symbol column
- [x] `<th>Side</th>` - Side column
- [x] `<th>Quantity</th>` - Quantity column
- [x] `<th>Entry Price</th>` - Entry price column
- [x] `<th>Exit Price</th>` - Exit price column
- [x] `<th>P&L</th>` - P&L column
- [x] `<th>P&L %</th>` - P&L percentage column
- [x] `<tbody id="backtestTradesBody">` - Backtest trades body
- [x] `<td colspan="8" class="text-center text-muted">No trades to display</td>` - No trades placeholder

## Login Modal

- [x] `<div class="modal fade" id="loginModal" tabindex="-1" style="display: none;">` - Login modal
- [x] `<div class="modal-dialog">` - Modal dialog
- [x] `<div class="modal-content">` - Modal content
- [x] `<div class="modal-header">` - Modal header
- [x] `<h5 class="modal-title">Trading Bot Authentication</h5>` - Modal title
- [x] `<div class="modal-body">` - Modal body
- [x] `<div class="alert alert-info">` - Info alert
- [x] `<form id="loginForm">` - Login form
- [x] `<div class="mb-3">` - Form group
- [x] `<label class="form-label">Solana Private Key</label>` - Private key label
- [x] `<input type="password" class="form-control" id="apiKeyInput" placeholder="Enter your Solana private key" required>` - Private key input
- [x] `<label class="form-label">Account Name (Optional)</label>` - Account name label
- [x] `<input type="text" class="form-control" id="apiSecretInput" placeholder="Enter account name" value="Default Account">` - Account name input
- [x] `<div class="form-check">` - Remember device checkbox
- [x] `<input class="form-check-input" type="checkbox" id="rememberDevice">` - Remember device input
- [x] `<label class="form-check-label" for="rememberDevice">` - Remember device label
- [x] `<div class="modal-footer">` - Modal footer
- [x] `<button type="button" class="btn btn-primary" onclick="secureLogin()">` - Login button
- [x] `<i class="fas fa-sign-in-alt"></i>` - Sign in icon

## JavaScript Scripts

- [x] `<script>...</script>` - Browser compatibility check script
- [x] `<script>...</script>` - Main application script (very extensive)
- [x] `<script>...</script>` - Account profiles initialization script

## Summary

This HTML file contains a comprehensive trading bot interface with the following major functional areas:

1. **Document Structure & Metadata** - DOCTYPE, HTML5 structure, meta tags, external resources
2. **Navigation & Layout** - Bootstrap navbar, quick summary bar, server startup section
3. **Authentication** - Compact auth forms, account switching, connection status
4. **Main Interface** - Tabbed interface with Dashboard, Risk, History, and Backtesting tabs
5. **Dashboard Features** - Control panel, metrics cards, collapsible sections, technical indicators, funding rates, balance history, market data, positions, performance charts
6. **Risk Management** - Portfolio risk metrics, position risk analysis, strategy configuration
7. **Trade History** - Filters, pagination, statistics, export functionality
8. **Backtesting** - Configuration form, status tracking, results display with charts and trade history
9. **Modals** - Position details, confirmation dialogs, login modal
10. **JavaScript** - Extensive client-side functionality for real-time updates, API calls, UI interactions

The interface uses Bootstrap 5 for styling, Font Awesome for icons, Chart.js and Lightweight Charts for data visualization, and includes comprehensive accessibility features, error handling, and responsive design.

<task_metadata>
session_id: ses_4f590856fffeNPn88lZHkW10l7
</task_metadata>