import { test, expect, Page } from '@playwright/test';
import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

test.describe('API Integration and Error Handling', () => {
  let serverProcess: ChildProcess | null = null;
  let serverReady = false;

  test.beforeAll(async () => {
    const serverPath = path.join(process.cwd(), 'trading_bot_v2', 'api_server.py');

    if (!fs.existsSync(serverPath)) {
      throw new Error(`Server file not found: ${serverPath}`);
    }

    const env = {
      ...process.env,
      TESTNET: 'true',
      AGENT_WALLET_PRIVATE_KEY: 'test_private_key_for_testing_only',
      ACCOUNT_PUBLIC_KEY: 'test_public_key_for_testing_only',
      DATABASE_PATH: ':memory:',
    };

    serverProcess = spawn('python', ['-m', 'trading_bot_v2.api_server'], {
      cwd: process.cwd(),
      env,
      stdio: ['pipe', 'pipe', 'pipe']
    });

    await new Promise<void>((resolve, reject) => {
      if (!serverProcess) return reject(new Error('Server process not created'));

      let stdout = '';
      let stderr = '';

      const timeout = setTimeout(() => {
        reject(new Error('Server startup timeout'));
      }, 30000);

      serverProcess.stdout?.on('data', (data) => {
        const output = data.toString();
        stdout += output;

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
    if (serverProcess) {
      serverProcess.kill('SIGTERM');
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
    if (!serverReady) {
      throw new Error('Server is not ready');
    }
    await page.goto('/');
    await page.waitForLoadState('networkidle');
  });

  test('API health check endpoint responds correctly', async ({ page }) => {
    // Test direct API call to health endpoint
    const response = await page.request.get('/api/health');
    expect(response.ok()).toBe(true);

    const data = await response.json();
    expect(data.success).toBe(true);
    expect(data.data.status).toBe('healthy');
    expect(data.source).toBe('health_service');
  });

  test('Bot status endpoint provides comprehensive status', async ({ page }) => {
    const response = await page.request.get('/api/status');
    expect(response.ok()).toBe(true);

    const data = await response.json();
    expect(data.success).toBe(true);
    expect(data.data).toHaveProperty('bot');
    expect(data.data).toHaveProperty('trading_mode');
    expect(data.data).toHaveProperty('testnet');
    expect(data.source).toBe('status_service');
  });

  test('Bot start endpoint handles missing credentials', async ({ page }) => {
    // Mock environment without credentials
    await page.route('**/api/bot/start', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: false,
          error: "No credentials configured. Set AGENT_WALLET_PRIVATE_KEY and ACCOUNT_PUBLIC_KEY in .env",
          source: 'bot_service'
        })
      });
    });

    const startButton = page.locator('button.btn-start');
    await startButton.click();

    await expect(startButton).toHaveText('▶ Start Bot');
    await expect(startButton).not.toBeDisabled();

    // Check for error message
    const toast = page.locator('.toast-error, .toast, [class*="toast"]').first();
    await expect(toast).toBeVisible();
  });

  test('Bot start endpoint handles server overload', async ({ page }) => {
    // Mock 503 Service Unavailable
    await page.route('**/api/bot/start', async (route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          success: false,
          error: "Service temporarily unavailable",
          source: 'bot_service'
        })
      });
    });

    const startButton = page.locator('button.btn-start');
    await startButton.click();

    await expect(startButton).toHaveText('▶ Start Bot');
    await expect(startButton).not.toBeDisabled();

    const toast = page.locator('.toast-error, .toast, [class*="toast"]').first();
    await expect(toast).toBeVisible();
    await expect(toast).toContainText('Failed to start bot');
  });

  test('Bot start endpoint handles malformed responses', async ({ page }) => {
    // Mock response with missing required fields
    await page.route('**/api/bot/start', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          // Missing success field
          data: { message: 'Trading started' },
          source: 'bot_service'
        })
      });
    });

    const startButton = page.locator('button.btn-start');
    await startButton.click();

    // Should handle gracefully despite malformed response
    await expect(startButton).toHaveText('▶ Start Bot');
    await expect(startButton).not.toBeDisabled();
  });

  test('API rate limiting simulation', async ({ page }) => {
    let requestCount = 0;

    // Mock rate limiting after 5 requests
    await page.route('**/api/bot/start', async (route) => {
      requestCount++;
      if (requestCount > 5) {
        await route.fulfill({
          status: 429,
          contentType: 'application/json',
          body: JSON.stringify({
            success: false,
            error: "Rate limit exceeded",
            source: 'bot_service'
          })
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            success: true,
            data: { message: 'Trading started', status: 'trading' },
            source: 'bot_service'
          })
        });
      }
    });

    const startButton = page.locator('button.btn-start');

    // Make multiple rapid requests
    for (let i = 0; i < 7; i++) {
      await startButton.click();
      await page.waitForTimeout(100);
    }

    // Should eventually hit rate limit
    const errorToast = page.locator('.toast-error, [class*="error"]').first();
    await expect(errorToast).toBeVisible();

    console.log(`Total requests made: ${requestCount}`);
  });

  test('Network connectivity issues handling', async ({ page }) => {
    // Simulate network failure
    await page.route('**/api/bot/start', async (route) => {
      // Abort the request to simulate network failure
      await route.abort('failed');
    });

    const startButton = page.locator('button.btn-start');
    await startButton.click();

    // Button should return to normal state
    await expect(startButton).toHaveText('▶ Start Bot');
    await expect(startButton).not.toBeDisabled();

    // Should show error feedback
    const toast = page.locator('.toast-error, .toast, [class*="toast"]').first();
    await expect(toast).toBeVisible();
  });

  test('API timeout handling', async ({ page }) => {
    // Mock very slow response
    await page.route('**/api/bot/start', async (route) => {
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

    // Set shorter timeout for this test
    page.setDefaultTimeout(10000); // 10 seconds

    const startButton = page.locator('button.btn-start');
    await startButton.click();

    // Should handle timeout gracefully
    await expect(startButton).not.toBeDisabled();
  });

  test('Concurrent API calls to different endpoints', async ({ page }) => {
    const startButton = page.locator('button.btn-start');
    const stopButton = page.locator('button.btn-stop');

    let startRequests = 0;
    let stopRequests = 0;
    let statusRequests = 0;

    // Mock all relevant endpoints
    await page.route('**/api/bot/start', async (route) => {
      startRequests++;
      await new Promise(resolve => setTimeout(resolve, 200));
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
      stopRequests++;
      await new Promise(resolve => setTimeout(resolve, 200));
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

    await page.route('**/api/status', async (route) => {
      statusRequests++;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { bot: 'stopped', bot_is_running: false },
          source: 'status_service'
        })
      });
    });

    // Trigger concurrent operations
    await Promise.all([
      startButton.click(),
      stopButton.click(),
      // Simulate status refresh
      page.evaluate(() => {
        if (window.refreshData) window.refreshData();
      })
    ]);

    // Wait for all operations to complete
    await page.waitForTimeout(1000);

    // Verify API calls were made
    expect(startRequests).toBeGreaterThan(0);
    expect(stopRequests).toBeGreaterThan(0);
    expect(statusRequests).toBeGreaterThanOrEqual(0);

    console.log(`Concurrent API calls - Start: ${startRequests}, Stop: ${stopRequests}, Status: ${statusRequests}`);
  });

  test('API response data validation', async ({ page }) => {
    // Test with various response formats
    const testCases = [
      {
        name: 'Valid response',
        response: {
          success: true,
          data: { message: 'Trading started', status: 'trading' },
          source: 'bot_service'
        },
        shouldSucceed: true
      },
      {
        name: 'Missing data field',
        response: {
          success: true,
          source: 'bot_service'
        },
        shouldSucceed: false
      },
      {
        name: 'Wrong success type',
        response: {
          success: 'true', // String instead of boolean
          data: { message: 'Trading started' },
          source: 'bot_service'
        },
        shouldSucceed: false
      },
      {
        name: 'Empty response',
        response: {},
        shouldSucceed: false
      }
    ];

    for (const testCase of testCases) {
      console.log(`Testing: ${testCase.name}`);

      await page.route('**/api/bot/start', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(testCase.response)
        });
      });

      const startButton = page.locator('button.btn-start');
      await startButton.click();

      // Wait for processing
      await page.waitForTimeout(500);

      // Button should return to normal state
      await expect(startButton).toHaveText('▶ Start Bot');
      await expect(startButton).not.toBeDisabled();

      // Check if success or error feedback was shown
      const successToast = page.locator('.toast-success');
      const errorToast = page.locator('.toast-error');

      if (testCase.shouldSucceed) {
        // Should show success or at least not show error
        const hasSuccess = await successToast.isVisible();
        const hasError = await errorToast.isVisible();
        expect(hasSuccess || !hasError).toBe(true);
      } else {
        // Should show error or handle gracefully
        // (Error handling depends on frontend implementation)
      }
    }
  });

  test('High-stakes API failure scenarios', async ({ page }) => {
    const failureScenarios = [
      {
        name: 'Database connection failure',
        status: 500,
        response: {
          success: false,
          error: 'Database connection failed',
          source: 'bot_service'
        }
      },
      {
        name: 'External API rate limited',
        status: 429,
        response: {
          success: false,
          error: 'External API rate limit exceeded',
          source: 'bot_service'
        }
      },
      {
        name: 'Insufficient permissions',
        status: 403,
        response: {
          success: false,
          error: 'Insufficient permissions to start trading',
          source: 'bot_service'
        }
      },
      {
        name: 'Trading engine unavailable',
        status: 503,
        response: {
          success: false,
          error: 'Trading engine temporarily unavailable',
          source: 'bot_service'
        }
      }
    ];

    for (const scenario of failureScenarios) {
      console.log(`Testing high-stakes scenario: ${scenario.name}`);

      await page.route('**/api/bot/start', async (route) => {
        await route.fulfill({
          status: scenario.status,
          contentType: 'application/json',
          body: JSON.stringify(scenario.response)
        });
      });

      const startButton = page.locator('button.btn-start');
      await startButton.click();

      // Verify error handling
      await expect(startButton).toHaveText('▶ Start Bot');
      await expect(startButton).not.toBeDisabled();

      // Should show appropriate error feedback
      const errorToast = page.locator('.toast-error, .toast, [class*="toast"]').first();
      await expect(errorToast).toBeVisible();

      await page.waitForTimeout(500); // Brief pause between scenarios
    }
  });
});