#!/bin/bash

# Trading Bot Playwright Test Runner
# This script sets up and runs the comprehensive Playwright test suite

set -e

echo "🚀 Setting up Trading Bot Playwright Tests"

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo "❌ Node.js is not installed. Please install Node.js 16+ first."
    exit 1
fi

# Check if Python is installed
if ! command -v python &> /dev/null; then
    echo "❌ Python is not installed. Please install Python 3.8+ first."
    exit 1
fi

# Install Node.js dependencies
echo "📦 Installing Node.js dependencies..."
npm install

# Install Playwright browsers
echo "🌐 Installing Playwright browsers..."
npm run install-browsers

# Check if trading bot server exists
if [ ! -f "trading_bot_v2/api_server.py" ]; then
    echo "❌ Trading bot server not found at trading_bot_v2/api_server.py"
    exit 1
fi

echo "✅ Setup complete!"
echo ""
echo "🎯 Available test commands:"
echo "  npm test              - Run all tests"
echo "  npm run test:headed   - Run tests with visible browser"
echo "  npm run test:ui       - Run tests with Playwright UI"
echo "  npm run test:debug    - Run tests in debug mode"
echo "  npm run report        - Show test report"
echo ""
echo "📊 Test categories:"
echo "  • Bot Controls        - Start/stop button functionality"
echo "  • WebSocket Updates   - Real-time communication"
echo "  • API Integration     - Endpoint testing and error handling"
echo "  • Performance         - Response times and resource usage"
echo ""
echo "🔧 Running tests now..."

# Run the tests
npm test