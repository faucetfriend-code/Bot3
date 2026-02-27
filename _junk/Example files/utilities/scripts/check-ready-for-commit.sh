#!/bin/bash
# Pre-commit Readiness Checker
# Verifies that code is ready for commit

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}🔍 Checking if code is ready for commit${NC}"
echo "=========================================="

# Detect Python command
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo -e "${RED}❌ Python not found${NC}"
    exit 1
fi

FAILED=false

# Run critical tests
echo -e "${YELLOW}🧪 Running critical tests...${NC}"
if $PYTHON_CMD -m pytest tests/test_critical_path.py -v --tb=short; then
    echo -e "${GREEN}✅ Critical tests passed${NC}"
else
    echo -e "${RED}❌ Critical tests failed${NC}"
    FAILED=true
fi

# Run linting
echo -e "${YELLOW}🔍 Running linting checks...${NC}"
if $PYTHON_CMD -m flake8 api_server.py database.py main.py config.py --max-line-length=88 --extend-ignore=E203,W503; then
    echo -e "${GREEN}✅ Linting passed${NC}"
else
    echo -e "${RED}❌ Linting failed${NC}"
    FAILED=true
fi

# Run type checking
echo -e "${YELLOW}🔍 Running type checking...${NC}"
if $PYTHON_CMD -m mypy api_server.py database.py main.py config.py --ignore-missing-imports; then
    echo -e "${GREEN}✅ Type checking passed${NC}"
else
    echo -e "${RED}❌ Type checking failed${NC}"
    FAILED=true
fi

# Check for uncommitted changes in critical files
echo -e "${YELLOW}📝 Checking for uncommitted critical files...${NC}"
CRITICAL_FILES=("api_server.py" "database.py" "main.py" "config.py")
for file in "${CRITICAL_FILES[@]}"; do
    if [[ -f "$file" ]] && ! git diff --quiet "$file"; then
        echo -e "${RED}❌ Uncommitted changes in $file${NC}"
        FAILED=true
    fi
done

if [[ "$FAILED" == "true" ]]; then
    echo ""
    echo -e "${RED}❌ Code is NOT ready for commit${NC}"
    echo -e "${BLUE}💡 Please fix the issues above and try again${NC}"
    exit 1
else
    echo ""
    echo -e "${GREEN}✅ Code is ready for commit!${NC}"
    echo -e "${BLUE}🚀 You can now commit your changes${NC}"
fi