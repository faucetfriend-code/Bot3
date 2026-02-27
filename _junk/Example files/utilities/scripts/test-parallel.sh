#!/bin/bash
# Parallel Test Runner
# Runs tests in parallel for faster feedback

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

echo -e "${BLUE}🚀 Running Tests in Parallel${NC}"
echo "============================="

# Check if pytest-xdist is available
if ! $PYTHON_CMD -c "import pytest_xdist" 2>/dev/null; then
    echo -e "${YELLOW}⚠️  pytest-xdist not available, installing...${NC}"
    $PYTHON_CMD -m pip install pytest-xdist
fi

# Set test environment
export TESTNET=true
export MOCK_TRADING=true
export DISABLE_EXTERNAL_APIS=true

# Detect number of CPU cores
if command -v nproc &> /dev/null; then
    CPU_CORES=$(nproc)
elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]]; then
    CPU_CORES=$NUMBER_OF_PROCESSORS
else
    CPU_CORES=2
fi

# Run tests in parallel
echo -e "${YELLOW}🧪 Running tests with $CPU_CORES workers...${NC}"
$PYTHON_CMD -m pytest tests/ -n $CPU_CORES --tb=short --strict-markers --disable-warnings --maxfail=10 --cov=. --cov-report=term-missing

echo -e "${GREEN}✅ All tests completed${NC}"