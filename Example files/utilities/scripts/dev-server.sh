#!/bin/bash
# Development Server with Safeguards
# Runs the API server with health checks and automatic testing

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}🚀 Starting Trading Bot Development Server${NC}"
echo "==========================================="

# Detect Python command
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo -e "${RED}❌ Python not found${NC}"
    exit 1
fi

# Run pre-flight checks
echo -e "${YELLOW}🔍 Running pre-flight checks...${NC}"

# Run critical tests
if ! $PYTHON_CMD scripts/run-critical-tests.sh; then
    echo -e "${RED}❌ Critical tests failed. Server will not start.${NC}"
    echo -e "${BLUE}💡 Fix the failing tests and try again.${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Pre-flight checks passed${NC}"

# Set development environment
export DEV_MODE=true
export AUTO_RELOAD=true
export LOG_LEVEL=DEBUG

# Run server with safeguards
echo -e "${YELLOW}🌐 Starting API server...${NC}"
echo -e "${BLUE}📊 Health checks will run automatically${NC}"
echo -e "${BLUE}🔄 Auto-reload enabled for development${NC}"
echo ""

# Start server
exec $PYTHON_CMD -m uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload --log-level debug