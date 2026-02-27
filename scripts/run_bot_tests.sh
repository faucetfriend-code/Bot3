#!/bin/bash

# Comprehensive Bot Controls Test Runner
# This script sets up the environment and runs the bot control tests

set -e  # Exit on any error

echo "🤖 Trading Bot Controls Test Suite"
echo "=================================="

# Check if we're in the right directory
if [ ! -f "trading_bot_v2/api_server.py" ]; then
    echo "❌ Error: Please run this script from the Bot 3 project root directory"
    exit 1
fi

# Set test environment variables
export TESTNET=true
export DATABASE_PATH=:memory:
export AGENT_WALLET_PRIVATE_KEY=test_private_key_placeholder
export ACCOUNT_PUBLIC_KEY=test_public_key_placeholder

echo "📋 Test Environment:"
echo "  - TESTNET: $TESTNET"
echo "  - Database: In-memory"
echo "  - Credentials: Test placeholders"
echo ""

echo "🧪 Running Bot Controls Tests..."
echo "=================================="

# Run the comprehensive tests
python -m pytest trading_bot_v2/test_bot_controls_comprehensive.py -v --tb=short

echo ""
echo "✅ Bot controls tests completed!"