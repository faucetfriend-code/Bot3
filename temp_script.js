        const API_BASE = '/api';
        let websocket = null;
        let reconnectAttempts = 0;
        const maxReconnectAttempts = 10;
        const baseReconnectDelay = 1000; // 1 second

        // Theme Management
        function toggleTheme() {
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
        }

        function loadTheme() {
            const savedTheme = localStorage.getItem('theme') || 'light';
            document.documentElement.setAttribute('data-theme', savedTheme);
        }

        // Toast Notification System
        function showToast(message, type = 'info', duration = 5000) {
            const toastContainer = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;

            const icons = {
                success: '✅',
                error: '❌',
                warning: '⚠️',
                info: 'ℹ️'
            };

            toast.innerHTML = `
                <span class="toast-icon">${icons[type] || icons.info}</span>
                <span class="toast-message">${message}</span>
                <button class="toast-close" onclick="this.parentElement.remove()">×</button>
            `;

            toastContainer.appendChild(toast);

            // Auto remove after duration
            setTimeout(() => {
                if (toast.parentElement) {
                    toast.classList.add('fade-out');
                    setTimeout(() => toast.remove(), 300);
                }
            }, duration);
        }

        // Loading States
        function setLoadingState(elementId, loading) {
            const element = document.getElementById(elementId);
            if (loading) {
                element.classList.add('loading');
            } else {
                element.classList.remove('loading');
            }
        }

        // Performance Monitoring
        let lastRefreshTime = 0;
        function updatePerformanceIndicator(duration) {
            lastRefreshTime = duration;
            const indicator = document.getElementById('performance-indicator');
            const timeSpan = document.getElementById('last-refresh-time');
            timeSpan.textContent = `${duration}ms`;
            indicator.style.display = 'block';
            setTimeout(() => indicator.style.display = 'none', 3000);
        }

        // WebSocket connection for real-time updates with exponential backoff
        function connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws`;

            websocket = new WebSocket(wsUrl);

            websocket.onopen = function(event) {
                console.log('WebSocket connected');
                const wsStatus = document.getElementById('ws-status');
                wsStatus.textContent = 'Connected';
                wsStatus.className = 'ws-status connected';
                reconnectAttempts = 0; // Reset on successful connection
                showToast('WebSocket connected', 'success', 3000);
            };

            websocket.onmessage = function(event) {
                try {
                    const message = JSON.parse(event.data);
                    handleRealtimeUpdate(message);
                } catch (e) {
                    console.error('Failed to parse WebSocket message:', e);
                    showToast('Failed to parse WebSocket message', 'error');
                }
            };

            websocket.onclose = function(event) {
                console.log('WebSocket disconnected');
                const wsStatus = document.getElementById('ws-status');
                wsStatus.textContent = 'Disconnected';
                wsStatus.className = 'ws-status disconnected';

                if (reconnectAttempts < maxReconnectAttempts) {
                    reconnectAttempts++;
                    const delay = Math.min(baseReconnectDelay * Math.pow(2, reconnectAttempts), 30000); // Max 30 seconds
                    console.log(`Attempting to reconnect in ${delay}ms (attempt ${reconnectAttempts}/${maxReconnectAttempts})`);
                    showToast(`WebSocket disconnected. Reconnecting in ${delay/1000}s...`, 'warning', 3000);
                    setTimeout(connectWebSocket, delay);
                } else {
                    showToast('WebSocket connection failed after multiple attempts', 'error');
                }
            };

            websocket.onerror = function(error) {
                console.error('WebSocket error:', error);
                showToast('WebSocket connection error', 'error');
            };
        }

        function handleRealtimeUpdate(message) {
            console.log('Real-time update:', message);

            switch(message.type) {
                case 'trade_executed':
                    // Refresh trades and positions when a trade is executed
                    updateTrades();
                    updatePositions();
                    updateStatus();
                    break;
                case 'grid_trade_executed':
                    // Refresh data for grid trades
                    updateTrades();
                    updatePositions();
                    updateStatus();
                    break;
                default:
                    console.log('Unknown update type:', message.type);
            }
        }

        async function apiCall(endpoint, method = 'GET', data = null) {
            const startTime = performance.now();
            try {
                const options = { method };
                if (data) {
                    options.headers = { 'Content-Type': 'application/json' };
                    options.body = JSON.stringify(data);
                }
                console.log(`Making API call to: ${API_BASE}${endpoint}`);
                const response = await fetch(`${API_BASE}${endpoint}`, options);
                if (!response.ok) {
                    const errorText = await response.text();
                    throw new Error(`HTTP ${response.status}: ${response.statusText} - ${errorText}`);
                }
                const result = await response.json();
                const duration = Math.round(performance.now() - startTime);
                console.log(`API call successful (${duration}ms):`, result);
                return result;
            } catch (error) {
                const duration = Math.round(performance.now() - startTime);
                console.error(`API call failed (${duration}ms):`, error);
                // Provide more specific error messages
                if (error.name === 'TypeError' && error.message.includes('fetch')) {
                    throw new Error('Cannot connect to server. Please ensure the API server is running on localhost:8000');
                }
                throw error;
            }
        }
        async function updateStatus() {
            try {
                const response = await apiCall('/status');
                const data = response.data;
                const statusSpan = document.getElementById('bot-status');
                statusSpan.textContent = data.bot_running ? 'Running' : 'Stopped';
                statusSpan.className = data.bot_running ? 'running' : 'stopped';
                document.getElementById('positions-count').textContent = data.positions_count || 0;
                document.getElementById('trades-count').textContent = data.trades_count || 0;
                document.getElementById('pnl').textContent = data.total_pnl ? data.total_pnl.toFixed(2) : '0.00';
            } catch (error) {
                console.error('Failed to update status:', error);
                // Show error state in UI
                const statusSpan = document.getElementById('bot-status');
                statusSpan.textContent = 'Error';
                statusSpan.className = 'error';
                document.getElementById('positions-count').textContent = 'N/A';
                document.getElementById('trades-count').textContent = 'N/A';
                document.getElementById('pnl').textContent = 'N/A';
                showToast(`Failed to update status: ${error.message}`, 'error');
            }
        }
        async function updatePositions() {
            try {
                const response = await apiCall('/positions');
                const data = response.data;
                const tbody = document.getElementById('positions-tbody');

                // Use document fragment to avoid layout thrashing
                const fragment = document.createDocumentFragment();

                if (data.length === 0) {
                    const row = document.createElement('tr');
                    const cell = document.createElement('td');
                    cell.colSpan = 8;
                    cell.style.textAlign = 'center';
                    cell.textContent = 'No open positions';
                    row.appendChild(cell);
                    fragment.appendChild(row);
                } else {
                    data.forEach(pos => {
                        const row = document.createElement('tr');

                        // Symbol
                        const symbolCell = document.createElement('td');
                        symbolCell.textContent = pos.symbol;
                        row.appendChild(symbolCell);

                        // Side with color
                        const sideCell = document.createElement('td');
                        sideCell.textContent = pos.side;
                        sideCell.className = pos.side === 'long' ? 'positive' : 'negative';
                        row.appendChild(sideCell);

                        // Quantity
                        const quantityCell = document.createElement('td');
                        quantityCell.textContent = pos.quantity;
                        row.appendChild(quantityCell);

                        // Entry Price
                        const entryCell = document.createElement('td');
                        entryCell.textContent = '$' + parseFloat(pos.entry_price).toFixed(2);
                        row.appendChild(entryCell);

                        // Current Price
                        const currentCell = document.createElement('td');
                        currentCell.textContent = pos.current_price ? '$' + parseFloat(pos.current_price).toFixed(2) : 'N/A';
                        row.appendChild(currentCell);

                        // Unrealized P&L with color
                        const unrealizedPnl = parseFloat(pos.unrealized_pnl || 0);
                        const unrealizedCell = document.createElement('td');
                        unrealizedCell.textContent = '$' + unrealizedPnl.toFixed(2);
                        unrealizedCell.className = unrealizedPnl >= 0 ? 'positive' : 'negative';
                        row.appendChild(unrealizedCell);

                        // Funding P&L with color
                        const fundingPnl = parseFloat(pos.funding_pnl || 0);
                        const fundingCell = document.createElement('td');
                        fundingCell.textContent = '$' + fundingPnl.toFixed(2);
                        fundingCell.className = fundingPnl >= 0 ? 'positive' : 'negative';
                        row.appendChild(fundingCell);

                        // Total P&L with color
                        const totalPnl = unrealizedPnl + fundingPnl;
                        const totalCell = document.createElement('td');
                        totalCell.textContent = '$' + totalPnl.toFixed(2);
                        totalCell.className = totalPnl >= 0 ? 'positive' : 'negative';
                        totalCell.style.fontSize = '1.1em';
                        row.appendChild(totalCell);

                        fragment.appendChild(row);
                    });
                }

                // Clear and append in one operation
                tbody.innerHTML = '';
                tbody.appendChild(fragment);
            } catch (error) {
                console.error('Failed to update positions:', error);
                showToast(`Failed to update positions: ${error.message}`, 'error');
            }
        }

        async function updateTrades() {
            try {
                const response = await apiCall('/trades?limit=20');
                const data = response.data || [];
                const tbody = document.getElementById('trades-tbody');

                // Use document fragment to avoid layout thrashing
                const fragment = document.createDocumentFragment();

                document.getElementById('trades-count').textContent = `(${data.length})`;

                if (data.length === 0) {
                    const row = document.createElement('tr');
                    const cell = document.createElement('td');
                    cell.colSpan = 7;
                    cell.style.textAlign = 'center';
                    cell.className = 'neutral';
                    cell.textContent = 'No trades yet';
                    row.appendChild(cell);
                    fragment.appendChild(row);
                } else {
                    data.forEach(trade => {
                        const row = document.createElement('tr');

                        // Symbol
                        const symbolCell = document.createElement('td');
                        symbolCell.textContent = trade.symbol;
                        row.appendChild(symbolCell);

                        // Side with color
                        const sideCell = document.createElement('td');
                        sideCell.textContent = trade.side;
                        sideCell.className = trade.side === 'BUY' ? 'positive' : 'negative';
                        row.appendChild(sideCell);

                        // Quantity
                        const quantityCell = document.createElement('td');
                        quantityCell.textContent = parseFloat(trade.quantity).toFixed(6);
                        row.appendChild(quantityCell);

                        // Price
                        const priceCell = document.createElement('td');
                        priceCell.textContent = '$' + parseFloat(trade.price).toLocaleString();
                        row.appendChild(priceCell);

                        // Fee
                        const feeCell = document.createElement('td');
                        const fee = parseFloat(trade.fee || 0);
                        feeCell.textContent = '$' + fee.toFixed(4);
                        feeCell.className = 'neutral';
                        row.appendChild(feeCell);

                        // P&L with color
                        const pnl = parseFloat(trade.pnl || 0);
                        const pnlCell = document.createElement('td');
                        pnlCell.textContent = '$' + pnl.toFixed(2);
                        pnlCell.className = pnl >= 0 ? 'positive' : 'negative';
                        row.appendChild(pnlCell);

                        // Time
                        const timeCell = document.createElement('td');
                        const timestamp = trade.timestamp;
                        timeCell.textContent = timestamp ? new Date(timestamp).toLocaleString() : 'N/A';
                        row.appendChild(timeCell);

                        fragment.appendChild(row);
                    });
                }

                // Clear and append in one operation
                tbody.innerHTML = '';
                tbody.appendChild(fragment);
            } catch (error) {
                console.error('Failed to update trades:', error);
                showToast(`Failed to update trades: ${error.message}`, 'error');
            }
        }

        async function updateActivity() {
            try {
                const response = await apiCall('/activity');
                const data = response.data || [];
                const errors = response.errors || [];
                const totalMarkets = response.total_markets || 0;
                const successful = response.successful || 0;
                const tbody = document.getElementById('activity-tbody');

                // Use document fragment to avoid layout thrashing
                const fragment = document.createDocumentFragment();

                // Log any errors to console for debugging
                if (errors.length > 0) {
                    console.warn('Activity fetch errors:', errors);
                    showToast(`${errors.length} markets failed to load`, 'warning', 3000);
                }

                if (data.length === 0) {
                    const row = document.createElement('tr');
                    const cell = document.createElement('td');
                    cell.colSpan = 7;
                    cell.style.textAlign = 'center';
                    cell.className = 'warning';
                    let errorMsg = 'No market data available';
                    if (errors.length > 0) {
                        errorMsg += ` (${errors.length} markets failed to load)`;
                    }
                    cell.textContent = errorMsg;
                    row.appendChild(cell);
                    fragment.appendChild(row);
                } else {
                    data.forEach(activity => {
                        const row = document.createElement('tr');

                        // Symbol
                        const symbolCell = document.createElement('td');
                        symbolCell.textContent = activity.symbol;
                        row.appendChild(symbolCell);

                        // Price
                        const priceCell = document.createElement('td');
                        priceCell.textContent = activity.price;
                        row.appendChild(priceCell);

                        // Regime (with color)
                        const regimeCell = document.createElement('td');
                        const regimeClass = 'regime-' + activity.regime.toLowerCase().replace(/ /g, '-');
                        regimeCell.innerHTML = `<span class="regime ${regimeClass}">${activity.regime}</span>`;
                        row.appendChild(regimeCell);

                        // RSI 15m (with color based on value)
                        const rsi15m = parseFloat(activity.rsi_15m);
                        const rsi15mCell = document.createElement('td');
                        let rsi15mClass = 'rsi-neutral';
                        if (rsi15m < 30) rsi15mClass = 'rsi-oversold';
                        else if (rsi15m > 70) rsi15mClass = 'rsi-overbought';
                        rsi15mCell.innerHTML = `<span class="${rsi15mClass}">${activity.rsi_15m}</span>`;
                        row.appendChild(rsi15mCell);

                        // RSI 1h (with color based on value)
                        const rsi1h = parseFloat(activity.rsi_1h);
                        const rsi1hCell = document.createElement('td');
                        let rsi1hClass = 'rsi-neutral';
                        if (rsi1h < 30) rsi1hClass = 'rsi-oversold';
                        else if (rsi1h > 70) rsi1hClass = 'rsi-overbought';
                        rsi1hCell.innerHTML = `<span class="${rsi1hClass}">${activity.rsi_1h}</span>`;
                        row.appendChild(rsi1hCell);

                        // Active Strategies
                        const strategiesCell = document.createElement('td');
                        strategiesCell.textContent = activity.active_strategies;
                        row.appendChild(strategiesCell);

                        // Status
                        const statusCell = document.createElement('td');
                        statusCell.textContent = activity.status;
                        row.appendChild(statusCell);

                        fragment.appendChild(row);
                    });
                }

                // Clear and append in one operation
                tbody.innerHTML = '';
                tbody.appendChild(fragment);

                // Update last update time with success/error stats
                let statusText = `Last updated: ${new Date().toLocaleTimeString()}`;
                if (totalMarkets > 0) {
                    statusText += ` (${successful}/${totalMarkets} markets)`;
                }
                document.getElementById('activity-last-update').textContent = statusText;

            } catch (error) {
                console.error('Failed to update activity:', error);
                showToast(`Failed to update activity: ${error.message}`, 'error');
            }
        }

        async function updateOrders() {
            try {
                const response = await apiCall('/orders');
                const data = response.data || [];
                const tbody = document.getElementById('orders-tbody');

                // Use document fragment to avoid layout thrashing
                const fragment = document.createDocumentFragment();

                document.getElementById('orders-count').textContent = `(${data.length})`;

                if (data.length === 0) {
                    const row = document.createElement('tr');
                    const cell = document.createElement('td');
                    cell.colSpan = 7;
                    cell.style.textAlign = 'center';
                    cell.className = 'neutral';
                    cell.textContent = 'No non-grid orders';
                    row.appendChild(cell);
                    fragment.appendChild(row);
                } else {
                    data.forEach(order => {
                        const row = document.createElement('tr');

                        // Symbol
                        const symbolCell = document.createElement('td');
                        symbolCell.textContent = order.symbol;
                        row.appendChild(symbolCell);

                        // Side with color
                        const sideCell = document.createElement('td');
                        sideCell.textContent = order.side;
                        sideCell.className = order.side === 'BUY' ? 'positive' : 'negative';
                        row.appendChild(sideCell);

                        // Type
                        const typeCell = document.createElement('td');
                        typeCell.textContent = order.type;
                        row.appendChild(typeCell);

                        // Price
                        const priceCell = document.createElement('td');
                        priceCell.textContent = '$' + parseFloat(order.price).toFixed(2);
                        row.appendChild(priceCell);

                        // Amount
                        const amountCell = document.createElement('td');
                        amountCell.textContent = parseFloat(order.amount).toFixed(6);
                        row.appendChild(amountCell);

                        // Filled
                        const filledCell = document.createElement('td');
                        filledCell.textContent = parseFloat(order.filled).toFixed(6);
                        row.appendChild(filledCell);

                        // Status
                        const statusCell = document.createElement('td');
                        statusCell.textContent = order.status;
                        statusCell.className = order.status === 'open' ? 'info' : 'neutral';
                        row.appendChild(statusCell);

                        fragment.appendChild(row);
                    });
                }

                // Clear and append in one operation
                tbody.innerHTML = '';
                tbody.appendChild(fragment);

                document.getElementById('orders-last-update').textContent = `Last updated: ${new Date().toLocaleTimeString()}`;

            } catch (error) {
                console.error('Failed to update orders:', error);
                showToast(`Failed to update orders: ${error.message}`, 'error');
            }
        }

        async function updateGrids() {
            try {
                const response = await apiCall('/grids');
                const data = response.data || [];
                const tbody = document.getElementById('grids-tbody');

                // Use document fragment to avoid layout thrashing
                const fragment = document.createDocumentFragment();

                document.getElementById('grids-count').textContent = `(${data.length})`;

                // Calculate total P&L across all grids
                let totalPnl = 0;
                data.forEach(g => totalPnl += (g.total_pnl || 0));
                const pnlSpan = document.getElementById('grids-pnl');
                if (totalPnl !== 0) {
                    pnlSpan.textContent = `P&L: $${totalPnl.toFixed(4)}`;
                    pnlSpan.className = totalPnl >= 0 ? 'positive' : 'negative';
                } else {
                    pnlSpan.textContent = '';
                }

                if (data.length === 0) {
                    const row = document.createElement('tr');
                    const cell = document.createElement('td');
                    cell.colSpan = 8;
                    cell.style.textAlign = 'center';
                    cell.textContent = 'No active grids';
                    row.appendChild(cell);
                    fragment.appendChild(row);
                } else {
                    data.forEach(grid => {
                        const row = document.createElement('tr');

                        // Symbol
                        const symbolCell = document.createElement('td');
                        symbolCell.textContent = grid.symbol;
                        row.appendChild(symbolCell);

                        // Orders (Buy/Sell)
                        const ordersCell = document.createElement('td');
                        ordersCell.innerHTML = `${grid.total_orders} (<span class="positive">${grid.buy_orders}B</span>/<span class="negative">${grid.sell_orders}S</span>)`;
                        row.appendChild(ordersCell);

                        // Price Range
                        const rangeCell = document.createElement('td');
                        rangeCell.textContent = `$${(grid.price_low || 0).toLocaleString()} - $${(grid.price_high || 0).toLocaleString()}`;
                        row.appendChild(rangeCell);

                        // Fills (Buy/Sell)
                        const fillsCell = document.createElement('td');
                        const buyFills = grid.total_buy_fills || 0;
                        const sellFills = grid.total_sell_fills || 0;
                        fillsCell.innerHTML = `<span class="positive">${buyFills}B</span>/<span class="negative">${sellFills}S</span>`;
                        if (buyFills > 0 || sellFills > 0) {
                            fillsCell.style.fontWeight = 'bold';
                        }
                        row.appendChild(fillsCell);

                        // Net Position
                        const netPosCell = document.createElement('td');
                        const netPos = grid.net_position || 0;
                        netPosCell.textContent = netPos.toFixed(6);
                        if (netPos > 0) {
                            netPosCell.className = 'positive';
                        } else if (netPos < 0) {
                            netPosCell.className = 'negative';
                        }
                        row.appendChild(netPosCell);

                        // Realized P&L
                        const realizedCell = document.createElement('td');
                        const realized = grid.realized_pnl || 0;
                        realizedCell.textContent = '$' + realized.toFixed(4);
                        realizedCell.className = realized >= 0 ? 'positive' : 'negative';
                        row.appendChild(realizedCell);

                        // Total P&L
                        const totalCell = document.createElement('td');
                        const total = grid.total_pnl || 0;
                        totalCell.textContent = '$' + total.toFixed(4);
                        totalCell.className = total >= 0 ? 'positive' : 'negative';
                        totalCell.style.fontWeight = 'bold';
                        row.appendChild(totalCell);

                        // Status
                        const statusCell = document.createElement('td');
                        const rounds = grid.completed_round_trips || 0;
                        if (rounds > 0) {
                            statusCell.innerHTML = `<span class="positive">${grid.status}</span> (${rounds} RT)`;
                        } else {
                            statusCell.textContent = grid.status;
                            statusCell.className = grid.status === 'active' ? 'positive' : 'neutral';
                        }
                        row.appendChild(statusCell);

                        fragment.appendChild(row);
                    });
                }

                // Clear and append in one operation
                tbody.innerHTML = '';
                tbody.appendChild(fragment);

            } catch (error) {
                console.error('Failed to update grids:', error);
                showToast(`Failed to update grids: ${error.message}`, 'error');
            }
        }

                        // Net Position
                        const netPosCell = row.insertCell();
                        const netPos = grid.net_position || 0;
                        netPosCell.textContent = netPos.toFixed(6);
                        if (netPos > 0) {
                            netPosCell.style.color = '#28a745';
                        } else if (netPos < 0) {
                            netPosCell.style.color = '#dc3545';
                        }

                        // Realized P&L
                        const realizedCell = row.insertCell();
                        const realized = grid.realized_pnl || 0;
                        realizedCell.textContent = '$' + realized.toFixed(4);
                        realizedCell.style.color = realized >= 0 ? '#28a745' : '#dc3545';

                        // Total P&L
                        const totalCell = row.insertCell();
                        const total = grid.total_pnl || 0;
                        totalCell.textContent = '$' + total.toFixed(4);
                        totalCell.style.color = total >= 0 ? '#28a745' : '#dc3545';
                        totalCell.style.fontWeight = 'bold';

                        // Status
                        const statusCell = document.createElement('td');
                        const rounds = grid.completed_round_trips || 0;
                        if (rounds > 0) {
                            statusCell.innerHTML = `<span class=\"positive\">${grid.status}</span> (${rounds} RT)`;
                        } else {
                            statusCell.textContent = grid.status;
                            statusCell.className = grid.status === 'active' ? 'positive' : 'neutral';
                        }
                    });
                }

            } catch (error) {
                console.error('Failed to update grids:', error);
                showToast(`Failed to update grids: ${error.message}`, 'error');
            }
        }

        async function refreshData() {
            const startTime = performance.now();
            setLoadingState('activity-table', true);
            setLoadingState('grids-table', true);
            setLoadingState('positions-table', true);
            setLoadingState('orders-table', true);
            setLoadingState('trades-table', true);

            try {
                await Promise.all([updateStatus(), updatePositions(), updateTrades(), updateActivity(), updateOrders(), updateGrids()]);
                const duration = Math.round(performance.now() - startTime);
                updatePerformanceIndicator(duration);
            } catch (error) {
                console.error('Error during refresh:', error);
                showToast('Failed to refresh data', 'error');
            } finally {
                setLoadingState('activity-table', false);
                setLoadingState('grids-table', false);
                setLoadingState('positions-table', false);
                setLoadingState('orders-table', false);
                setLoadingState('trades-table', false);
            }
        }
        async function startBot() {
            const button = document.querySelector('button[onclick="startBot()"]');
            const originalText = button.textContent;
            try {
                button.textContent = 'Starting...';
                button.disabled = true;

                const response = await apiCall('/bot/start', 'POST');
                showToast(response.message, 'success');
                setTimeout(refreshData, 1000);
            } catch (error) {
                showToast('Failed to start bot: ' + error.message, 'error');
                console.error('Start bot error:', error);
            } finally {
                button.textContent = originalText;
                button.disabled = false;
            }
        }

        async function stopBot() {
            const button = document.querySelector('button[onclick="stopBot()"]');
            const originalText = button.textContent;
            try {
                button.textContent = 'Stopping...';
                button.disabled = true;

                const response = await apiCall('/bot/stop', 'POST');
                showToast(response.message, 'success');
                setTimeout(refreshData, 1000);
            } catch (error) {
                showToast('Failed to stop bot: ' + error.message, 'error');
                console.error('Stop bot error:', error);
            } finally {
                button.textContent = originalText;
                button.disabled = false;
            }
        }
        }

        async function syncPositions() {
            const button = document.querySelector('button[onclick="syncPositions()"]');
            const originalText = button.textContent;
            try {
                button.textContent = 'Syncing...';
                button.disabled = true;

                const response = await apiCall('/positions/sync', 'GET');
                if (response.success) {
                    showToast(`Positions synced successfully. Synced ${response.synced_from_pacifica || 0} positions from Pacifica.`, 'success');
                } else {
                    showToast('Sync failed: ' + response.message, 'error');
                }
                setTimeout(refreshData, 1000);
            } catch (error) {
                showToast('Failed to sync positions: ' + error.message, 'error');
                console.error('Sync positions error:', error);
            } finally {
                button.textContent = originalText;
                button.disabled = false;
            }
        }
        }
        window.onload = function() {
            // Load saved theme
            loadTheme();

            // Connect to WebSocket for real-time updates
            connectWebSocket();

            // Initial data load
            refreshData();

            // Reduced polling frequency since we have real-time updates
            setInterval(refreshData, 300000);  // 5 minutes instead of 1 minute
        };
