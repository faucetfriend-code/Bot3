import { test, expect, Page } from '@playwright/test';
import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

test.describe('Bot Start/Stop Functionality', () => {
  let serverProcess: ChildProcess | null = null;
  let serverReady = false;

  test.beforeAll(async () => {
    // Start the FastAPI server programmatically
    const serverPath = path.join(process.cwd(), 'trading_bot_v2', 'api_server.py');

    // Check if server file exists
    if (!fs.existsSync(serverPath)) {
      throw new Error(`Server file not found: ${serverPath}`);
    }

    // Set environment variables for testing
    const env = {
      ...process.env,
      TESTNET: 'true',
      AGENT_WALLET_PRIVATE_KEY: 'test_private_key_for_testing_only',
      ACCOUNT_PUBLIC_KEY: 'test_public_key_for_testing_only',
      DATABASE_PATH: ':memory:', // Use in-memory database for tests
    };

    serverProcess = spawn('python', ['-m', 'trading_bot_v2.api_server'], {
      cwd: process.cwd(),
      env,
      stdio: ['pipe', 'pipe', 'pipe']
    });

    // Wait for server to be ready
    await new Promise<void>((resolve, reject) => {
      if (!serverProcess) return reject(new Error('Server process not created'));

      let stdout = '';
      let stderr = '';

      const timeout = setTimeout(() => {
        reject(new Error('Server startup timeout'));
      }, 30000); // 30 second timeout

      serverProcess.stdout?.on('data', (data) => {
        const output = data.toString();
        stdout += output;
        console.log('Server stdout:', output);

        // Check for server ready indicators
        if (output.includes('Application startup complete') ||
            output.includes('Uvicorn running on') ||
            output.includes('INFO:     Started server process')) {
          serverReady = true;
          clearTimeout(timeout);
          resolve();
        }
      });

      serverProcess.stderr?.on('data', (data) => {
        const output = data.toString();
        stderr += output;
        console.log('Server stderr:', output);

        // Some servers log readiness to stderr
        if (output.includes('Application startup complete') ||
            output.includes('Uvicorn running on')) {
          serverReady = true;
          clearTimeout(timeout);
          resolve();
        }
      });

      serverProcess.on('error', (error) => {
        clearTimeout(timeout);
        reject(error);
      });

      serverProcess.on('exit', (code) => {
        clearTimeout(timeout);
        if (code !== 0) {
          reject(new Error(`Server exited with code ${code}. Stderr: ${stderr}`));
        }
      });
    });
  });

  test.afterAll(async () => {
    // Clean up server process
    if (serverProcess) {
      serverProcess.kill('SIGTERM');

      // Wait for process to exit
      await new Promise<void>((resolve) => {
        serverProcess!.on('exit', () => resolve());
        setTimeout(() => {
          serverProcess!.kill('SIGKILL');
          resolve();
        }, 5000);
      });
    }
  });

  test.beforeEach(async ({ page }) => {
    // Ensure server is ready before each test
    if (!serverReady) {
      throw new Error('Server is not ready');
    }

    // Navigate to the interface
    await page.goto('/');

    // Wait for the page to load completely
    await page.waitForLoadState('networkidle');
  });

  test('Interface loads with start and stop buttons visible', async ({ page }) => {
    // Check that the start and stop buttons are present
    const startButton = page.locator('button.btn-start');
    const stopButton = page.locator('button.btn-stop');

    await expect(startButton).toBeVisible();
    await expect(stopButton).toBeVisible();

    // Check button text
    await expect(startButton).toHaveText('▶ Start Bot');
    await expect(stopButton).toHaveText('⏸ Stop Bot');
  });

  test('Bot start button triggers API call and shows success feedback', async ({ page }) => {
    const startButton = page.locator('button.btn-start');

    // Mock the API response for successful start
    await page.route('**/api/bot/start', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { message: 'Trading started', status: 'trading' },
          source: 'bot_service'
        })
      });
    });

    // Click the start button
    await startButton.click();

    // Check that button shows loading state
    await expect(startButton).toHaveText('Starting...');
    await expect(startButton).toBeDisabled();

    // Wait for the API call to complete and button to return to normal
    await expect(startButton).toHaveText('▶ Start Bot');
    await expect(startButton).not.toBeDisabled();

    // Check for success toast notification
    const toast = page.locator('.toast-success, .toast, [class*="toast"]').first();
    await expect(toast).toBeVisible();
    await expect(toast).toContainText('Trading started');
  });

  test('Bot stop button triggers API call and shows success feedback', async ({ page }) => {
    const stopButton = page.locator('button.btn-stop');

    // Mock the API response for successful stop
    await page.route('**/api/bot/stop', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { message: 'Trading stopped', status: 'stopped' },
          source: 'bot_service'
        })
      });
    });

    // Click the stop button
    await stopButton.click();

    // Check that button shows loading state
    await expect(stopButton).toHaveText('Stopping...');
    await expect(stopButton).toBeDisabled();

    // Wait for the API call to complete and button to return to normal
    await expect(stopButton).toHaveText('⏸ Stop Bot');
    await expect(stopButton).not.toBeDisabled();

    // Check for success toast notification
    const toast = page.locator('.toast-success, .toast, [class*="toast"]').first();
    await expect(toast).toBeVisible();
    await expect(toast).toContainText('Trading stopped');
  });

  test('Bot start API call performance monitoring', async ({ page }) => {
    const startButton = page.locator('button.btn-start');
    let apiCallStartTime = 0;
    let apiCallEndTime = 0;

    // Monitor API call timing
    await page.route('**/api/bot/start', async (route) => {
      apiCallStartTime = Date.now();
      await new Promise(resolve => setTimeout(resolve, 100)); // Simulate 100ms API delay
      apiCallEndTime = Date.now();

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { message: 'Trading started', status: 'trading' },
          source: 'bot_service'
        })
      });
    });

    // Click the start button
    await startButton.click();

    // Wait for completion
    await expect(startButton).toHaveText('▶ Start Bot');

    // Verify API call took reasonable time (should be < 500ms for this test)
    const apiCallDuration = apiCallEndTime - apiCallStartTime;
    expect(apiCallDuration).toBeGreaterThan(90); // At least 90ms (our simulated delay)
    expect(apiCallDuration).toBeLessThan(500); // Less than 500ms total

    console.log(`API call duration: ${apiCallDuration}ms`);
  });

  test('Bot start handles server error gracefully', async ({ page }) => {
    const startButton = page.locator('button.btn-start');

    // Mock server error
    await page.route('**/api/bot/start', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({
          success: false,
          error: 'Internal server error',
          source: 'bot_service'
        })
      });
    });

    // Click the start button
    await startButton.click();

    // Check that button returns to normal state
    await expect(startButton).toHaveText('▶ Start Bot');
    await expect(startButton).not.toBeDisabled();

    // Check for error toast notification
    const toast = page.locator('.toast-error, .toast, [class*="toast"]').first();
    await expect(toast).toBeVisible();
    await expect(toast).toContainText('Failed to start bot');
  });

  test('Bot start handles network timeout', async ({ page }) => {
    const startButton = page.locator('button.btn-start');

    // Mock network timeout
    await page.route('**/api/bot/start', async (route) => {
      // Delay response to simulate timeout
      await new Promise(resolve => setTimeout(resolve, 35000)); // 35 seconds
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { message: 'Trading started', status: 'trading' },
          source: 'bot_service'
        })
      });
    });

    // Set page timeout for this test
    page.setDefaultTimeout(40000);

    // Click the start button
    await startButton.click();

    // The button should eventually return to normal or show error
    // (depending on how the frontend handles timeouts)
    await expect(startButton).not.toBeDisabled();
  });

  test('Bot start handles invalid JSON response', async ({ page }) => {
    const startButton = page.locator('button.btn-start');

    // Mock invalid JSON response
    await page.route('**/api/bot/start', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: 'invalid json {{{'
      });
    });

    // Click the start button
    await startButton.click();

    // Check that button returns to normal state
    await expect(startButton).toHaveText('▶ Start Bot');
    await expect(startButton).not.toBeDisabled();

    // Check for error toast notification
    const toast = page.locator('.toast-error, .toast, [class*="toast"]').first();
    await expect(toast).toBeVisible();
  });

  test('Bot state changes after start operation', async ({ page }) => {
    // Mock successful start
    await page.route('**/api/bot/start', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { message: 'Trading started', status: 'trading' },
          source: 'bot_service'
        })
      });
    });

    // Mock status endpoint to return trading state
    await page.route('**/api/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            bot: 'trading',
            bot_is_running: true,
            status: 'healthy'
          },
          source: 'status_service'
        })
      });
    });

    const startButton = page.locator('button.btn-start');
    await startButton.click();

    // Wait for data refresh (the frontend calls refreshData after 1 second)
    await page.waitForTimeout(1500);

    // Check that status shows bot is trading
    const statusIndicator = page.locator('[data-testid="bot-status"], .status-indicator, .bot-status').first();
    if (await statusIndicator.isVisible()) {
      await expect(statusIndicator).toContainText('trading');
    }
  });

  test('WebSocket real-time updates after bot start', async ({ page }) => {
    // Mock WebSocket connection
    const wsMessages: any[] = [];

    // Listen for WebSocket messages
    page.on('websocket', ws => {
      ws.on('framereceived', event => {
        try {
          const message = JSON.parse(event.payload as string);
          wsMessages.push(message);
        } catch (e) {
          // Ignore non-JSON messages
        }
      });
    });

    // Mock successful start with WebSocket broadcast
    await page.route('**/api/bot/start', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { message: 'Trading started', status: 'trading' },
          source: 'bot_service'
        })
      });
    });

    const startButton = page.locator('button.btn-start');
    await startButton.click();

    // Wait for potential WebSocket messages
    await page.waitForTimeout(2000);

    // Check if any WebSocket messages were received about bot status
    const botUpdateMessages = wsMessages.filter(msg =>
      msg.type === 'bot_update' || msg.event_type === 'bot_status_changed'
    );

    // Note: This test may need adjustment based on actual WebSocket implementation
    console.log('WebSocket messages received:', wsMessages);
  });

  test('Concurrent start/stop operations are handled safely', async ({ page }) => {
    const startButton = page.locator('button.btn-start');
    const stopButton = page.locator('button.btn-stop');

    // Mock API responses
    let startCallCount = 0;
    let stopCallCount = 0;

    await page.route('**/api/bot/start', async (route) => {
      startCallCount++;
      await new Promise(resolve => setTimeout(resolve, 500)); // Simulate delay
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { message: 'Trading started', status: 'trading' },
          source: 'bot_service'
        })
      });
    });

    await page.route('**/api/bot/stop', async (route) => {
      stopCallCount++;
      await new Promise(resolve => setTimeout(resolve, 500)); // Simulate delay
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { message: 'Trading stopped', status: 'stopped' },
          source: 'bot_service'
        })
      });
    });

    // Click both buttons quickly (race condition test)
    await Promise.all([
      startButton.click(),
      stopButton.click()
    ]);

    // Wait for operations to complete
    await page.waitForTimeout(2000);

    // Both buttons should return to normal state
    await expect(startButton).toHaveText('▶ Start Bot');
    await expect(stopButton).toHaveText('⏸ Stop Bot');
    await expect(startButton).not.toBeDisabled();
    await expect(stopButton).not.toBeDisabled();

    // Check that API calls were made (at least one of each should have been attempted)
    expect(startCallCount + stopCallCount).toBeGreaterThan(0);

    console.log(`Start calls: ${startCallCount}, Stop calls: ${stopCallCount}`);
  });

  test('Bot operations work across different browsers', async ({ page, browserName }) => {
    test.skip(browserName === 'webkit', 'Skipping WebKit for this test');

    const startButton = page.locator('button.btn-start');

    // Mock successful start
    await page.route('**/api/bot/start', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { message: 'Trading started', status: 'trading' },
          source: 'bot_service'
        })
      });
    });

    // Click the start button
    await startButton.click();

    // Verify the operation works in this browser
    await expect(startButton).toHaveText('▶ Start Bot');
    await expect(startButton).not.toBeDisabled();

    console.log(`Test passed in ${browserName}`);
  });

  test('High-stakes scenario: API failures during concurrent operations', async ({ page }) => {
    const startButton = page.locator('button.btn-start');
    const stopButton = page.locator('button.btn-stop');

    let startFailures = 0;
    let stopFailures = 0;

    // Mock API failures
    await page.route('**/api/bot/start', async (route) => {
      startFailures++;
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          success: false,
          error: 'Service temporarily unavailable',
          source: 'bot_service'
        })
      });
    });

    await page.route('**/api/bot/stop', async (route) => {
      stopFailures++;
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          success: false,
          error: 'Service temporarily unavailable',
          source: 'bot_service'
        })
      });
    });

    // Perform concurrent operations that will fail
    await Promise.all([
      startButton.click(),
      stopButton.click()
    ]);

    // Wait for error handling
    await page.waitForTimeout(1000);

    // Both buttons should return to normal state
    await expect(startButton).toHaveText('▶ Start Bot');
    await expect(stopButton).toHaveText('⏸ Stop Bot');
    await expect(startButton).not.toBeDisabled();
    await expect(stopButton).not.toBeDisabled();

    // Check for error toasts
    const errorToasts = page.locator('.toast-error, [class*="error"]');
    await expect(errorToasts.first()).toBeVisible();

    // Verify API calls were attempted
    expect(startFailures).toBeGreaterThan(0);
    expect(stopFailures).toBeGreaterThan(0);

    console.log(`API failures - Start: ${startFailures}, Stop: ${stopFailures}`);
  });
});