#!/bin/bash
# Critical Tests Runner
# Runs the most important tests that must pass for server stability

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Detect Python command
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo -e "${RED}❌ Python not found${NC}"
    exit 1
fi

echo -e "${BLUE}🧪 Running Critical Path Tests${NC}"
echo "==============================="

# Set test environment
export TESTNET=true
export MOCK_TRADING=true
export DISABLE_EXTERNAL_APIS=true
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=true

# Run critical tests with timeout
timeout 300 $PYTHON_CMD -m pytest tests/test_critical_path.py tests/test_server_safeguards.py -v --tb=short --strict-markers --disable-warnings --maxfail=5

echo -e "${GREEN}✅ All critical tests passed${NC}"