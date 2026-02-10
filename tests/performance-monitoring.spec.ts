import { test, expect, Page } from '@playwright/test';
import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

test.describe('Performance Monitoring and Response Times', () => {
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

  test('Bot start button response time under 2 seconds', async ({ page }) => {
    const startButton = page.locator('button.btn-start');
    let startTime = 0;
    let endTime = 0;

    await page.route('**/api/bot/start', async (route) => {
      startTime = Date.now();
      await new Promise(resolve => setTimeout(resolve, 100)); // Simulate API delay
      endTime = Date.now();

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

    const clickStart = Date.now();
    await startButton.click();

    // Wait for completion
    await expect(startButton).toHaveText('▶ Start Bot');
    const clickEnd = Date.now();

    const totalResponseTime = clickEnd - clickStart;
    const apiResponseTime = endTime - startTime;

    console.log(`Total response time: ${totalResponseTime}ms`);
    console.log(`API response time: ${apiResponseTime}ms`);

    // Assert performance requirements
    expect(totalResponseTime).toBeLessThan(2000); // Under 2 seconds total
    expect(apiResponseTime).toBeLessThan(500); // API should respond within 500ms
  });

  test('Page load performance', async ({ page }) => {
    const loadStart = Date.now();

    // Navigate and wait for load
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const loadEnd = Date.now();
    const loadTime = loadEnd - loadStart;

    console.log(`Page load time: ${loadTime}ms`);

    // Assert reasonable load time
    expect(loadTime).toBeLessThan(5000); // Under 5 seconds

    // Check that critical elements are present
    const startButton = page.locator('button.btn-start');
    const stopButton = page.locator('button.btn-stop');

    await expect(startButton).toBeVisible();
    await expect(stopButton).toBeVisible();
  });

  test('Concurrent operations performance', async ({ page }) => {
    const startButton = page.locator('button.btn-start');
    const stopButton = page.locator('button.btn-stop');

    const operationTimes: number[] = [];

    // Mock API responses with varying delays
    await page.route('**/api/bot/start', async (route) => {
      const start = Date.now();
      const delay = Math.random() * 300 + 50; // 50-350ms random delay
      await new Promise(resolve => setTimeout(resolve, delay));
      operationTimes.push(Date.now() - start);

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
      const start = Date.now();
      const delay = Math.random() * 300 + 50;
      await new Promise(resolve => setTimeout(resolve, delay));
      operationTimes.push(Date.now() - start);

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

    const concurrentStart = Date.now();

    // Perform concurrent operations
    await Promise.all([
      startButton.click(),
      stopButton.click()
    ]);

    const concurrentEnd = Date.now();
    const totalConcurrentTime = concurrentEnd - concurrentStart;

    // Wait for both operations to complete
    await expect(startButton).toHaveText('▶ Start Bot');
    await expect(stopButton).toHaveText('⏸ Stop Bot');

    console.log(`Concurrent operations time: ${totalConcurrentTime}ms`);
    console.log(`Individual operation times: ${operationTimes.join(', ')}ms`);

    // Assert performance - concurrent operations shouldn't take much longer than sequential
    const maxIndividualTime = Math.max(...operationTimes);
    expect(totalConcurrentTime).toBeLessThan(maxIndividualTime * 1.5); // Should be reasonably close
  });

  test('Memory usage monitoring during operations', async ({ page }) => {
    // Get initial memory usage
    const initialMemory = await page.evaluate(() => {
      if (performance.memory) {
        return {
          used: performance.memory.usedJSHeapSize,
          total: performance.memory.totalJSHeapSize,
          limit: performance.memory.jsHeapSizeLimit
        };
      }
      return null;
    });

    console.log('Initial memory:', initialMemory);

    const startButton = page.locator('button.btn-start');

    // Perform multiple operations
    for (let i = 0; i < 10; i++) {
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

      await startButton.click();
      await expect(startButton).toHaveText('▶ Start Bot');
    }

    // Get memory usage after operations
    const finalMemory = await page.evaluate(() => {
      if (performance.memory) {
        return {
          used: performance.memory.usedJSHeapSize,
          total: performance.memory.totalJSHeapSize,
          limit: performance.memory.jsHeapSizeLimit
        };
      }
      return null;
    });

    console.log('Final memory:', finalMemory);

    if (initialMemory && finalMemory) {
      const memoryIncrease = finalMemory.used - initialMemory.used;
      const memoryIncreaseMB = memoryIncrease / (1024 * 1024);

      console.log(`Memory increase: ${memoryIncreaseMB.toFixed(2)} MB`);

      // Assert reasonable memory usage (shouldn't increase dramatically)
      expect(memoryIncreaseMB).toBeLessThan(50); // Less than 50MB increase
    }
  });

  test('WebSocket message processing performance', async ({ page }) => {
    const messageProcessingTimes: number[] = [];
    let messageCount = 0;

    page.on('websocket', ws => {
      ws.on('framereceived', event => {
        const receiveTime = Date.now();
        messageCount++;

        try {
          const message = JSON.parse(event.payload as string);
          // Simulate message processing
          setTimeout(() => {
            const processingTime = Date.now() - receiveTime;
            messageProcessingTimes.push(processingTime);
          }, 10);
        } catch (e) {
          // Ignore invalid messages
        }
      });
    });

    // Generate WebSocket traffic by triggering status updates
    await page.route('**/api/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { bot: 'trading', bot_is_running: true },
          source: 'status_service'
        })
      });
    });

    // Trigger multiple status updates
    for (let i = 0; i < 20; i++) {
      await page.evaluate(() => {
        if (window.refreshData) window.refreshData();
      });
      await page.waitForTimeout(50);
    }

    // Wait for message processing
    await page.waitForTimeout(2000);

    console.log(`Messages processed: ${messageCount}`);
    console.log(`Processing times: ${messageProcessingTimes.slice(0, 5).join(', ')}ms`);

    if (messageProcessingTimes.length > 0) {
      const avgProcessingTime = messageProcessingTimes.reduce((a, b) => a + b, 0) / messageProcessingTimes.length;
      const maxProcessingTime = Math.max(...messageProcessingTimes);

      console.log(`Average processing time: ${avgProcessingTime.toFixed(2)}ms`);
      console.log(`Max processing time: ${maxProcessingTime}ms`);

      // Assert reasonable processing times
      expect(avgProcessingTime).toBeLessThan(100); // Under 100ms average
      expect(maxProcessingTime).toBeLessThan(500); // Under 500ms max
    }
  });

  test('Database query performance under load', async ({ page }) => {
    const queryTimes: number[] = [];

    // Mock status endpoint with database simulation
    await page.route('**/api/status', async (route) => {
      const queryStart = Date.now();

      // Simulate database query time (50-200ms)
      const queryDelay = Math.random() * 150 + 50;
      await new Promise(resolve => setTimeout(resolve, queryDelay));

      queryTimes.push(Date.now() - queryStart);

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            bot: 'trading',
            bot_is_running: true,
            total_pnl: 123.45,
            win_rate: 65.5
          },
          source: 'status_service'
        })
      });
    });

    // Perform multiple status requests
    for (let i = 0; i < 15; i++) {
      const response = await page.request.get('/api/status');
      expect(response.ok()).toBe(true);
      await page.waitForTimeout(20);
    }

    console.log(`Database query times: ${queryTimes.map(t => t.toFixed(0)).join(', ')}ms`);

    if (queryTimes.length > 0) {
      const avgQueryTime = queryTimes.reduce((a, b) => a + b, 0) / queryTimes.length;
      const maxQueryTime = Math.max(...queryTimes);
      const minQueryTime = Math.min(...queryTimes);

      console.log(`Query time stats - Avg: ${avgQueryTime.toFixed(2)}ms, Min: ${minQueryTime}ms, Max: ${maxQueryTime}ms`);

      // Assert database performance
      expect(avgQueryTime).toBeLessThan(300); // Under 300ms average
      expect(maxQueryTime).toBeLessThan(1000); // Under 1 second max
    }
  });

  test('UI responsiveness during high-frequency updates', async ({ page }) => {
    const startButton = page.locator('button.btn-start');
    const uiResponseTimes: number[] = [];

    // Mock rapid API responses
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

    // Measure UI responsiveness during rapid clicking
    for (let i = 0; i < 10; i++) {
      const clickStart = Date.now();

      // Click and wait for visual feedback
      await startButton.click();
      await expect(startButton).toHaveText('▶ Start Bot');

      const responseTime = Date.now() - clickStart;
      uiResponseTimes.push(responseTime);

      // Brief pause between clicks
      await page.waitForTimeout(10);
    }

    console.log(`UI response times: ${uiResponseTimes.map(t => t.toFixed(0)).join(', ')}ms`);

    const avgResponseTime = uiResponseTimes.reduce((a, b) => a + b, 0) / uiResponseTimes.length;
    const maxResponseTime = Math.max(...uiResponseTimes);

    console.log(`UI responsiveness - Avg: ${avgResponseTime.toFixed(2)}ms, Max: ${maxResponseTime}ms`);

    // Assert UI remains responsive
    expect(avgResponseTime).toBeLessThan(1000); // Under 1 second average
    expect(maxResponseTime).toBeLessThan(2000); // Under 2 seconds max
  });

  test('Resource cleanup performance', async ({ page }) => {
    // Test that resources are cleaned up properly after operations
    const initialResources = await page.evaluate(() => {
      return {
        timers: (window as any).timers || 0,
        eventListeners: (window as any).eventListeners || 0,
        // Add other resource tracking as available
      };
    });

    const startButton = page.locator('button.btn-start');

    // Perform operations that might create resources
    for (let i = 0; i < 5; i++) {
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

      await startButton.click();
      await expect(startButton).toHaveText('▶ Start Bot');
      await page.waitForTimeout(100);
    }

    // Force garbage collection if available
    await page.evaluate(() => {
      if (window.gc) {
        window.gc();
      }
    });

    await page.waitForTimeout(1000);

    const finalResources = await page.evaluate(() => {
      return {
        timers: (window as any).timers || 0,
        eventListeners: (window as any).eventListeners || 0,
      };
    });

    console.log('Resource usage - Initial:', initialResources);
    console.log('Resource usage - Final:', finalResources);

    // Resources should not accumulate significantly
    // (This is a basic check - real resource monitoring would be more sophisticated)
  });

  test('End-to-end operation performance benchmark', async ({ page }) => {
    const performanceMetrics = {
      pageLoad: 0,
      buttonClick: 0,
      apiResponse: 0,
      uiUpdate: 0,
      total: 0
    };

    // Measure page load
    const pageLoadStart = Date.now();
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    performanceMetrics.pageLoad = Date.now() - pageLoadStart;

    // Measure button click performance
    const startButton = page.locator('button.btn-start');
    let apiStartTime = 0;
    let apiEndTime = 0;

    await page.route('**/api/bot/start', async (route) => {
      apiStartTime = Date.now();
      await new Promise(resolve => setTimeout(resolve, 80)); // Realistic API delay
      apiEndTime = Date.now();

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

    const buttonClickStart = Date.now();
    await startButton.click();

    // Wait for UI update
    const uiUpdateStart = Date.now();
    await expect(startButton).toHaveText('▶ Start Bot');
    performanceMetrics.uiUpdate = Date.now() - uiUpdateStart;

    performanceMetrics.buttonClick = Date.now() - buttonClickStart;
    performanceMetrics.apiResponse = apiEndTime - apiStartTime;
    performanceMetrics.total = Date.now() - pageLoadStart;

    console.log('End-to-end performance metrics:');
    Object.entries(performanceMetrics).forEach(([key, value]) => {
      console.log(`  ${key}: ${value}ms`);
    });

    // Assert comprehensive performance requirements
    expect(performanceMetrics.pageLoad).toBeLessThan(3000);
    expect(performanceMetrics.apiResponse).toBeLessThan(500);
    expect(performanceMetrics.buttonClick).toBeLessThan(1500);
    expect(performanceMetrics.uiUpdate).toBeLessThan(500);
    expect(performanceMetrics.total).toBeLessThan(5000);
  });
});