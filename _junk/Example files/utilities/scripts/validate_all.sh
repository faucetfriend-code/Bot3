#!/bin/bash
# Run all validation agents on the project

set -e  # Exit on error

echo "🔍 Trading Bot - Running All Validators"
echo "========================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if we're in the project root
if [ ! -f "agents/base_agent.py" ]; then
    echo -e "${RED}Error: Must run from project root directory${NC}"
    exit 1
fi

# Create reports directory
mkdir -p reports
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
REPORT_DIR="reports/validation_$TIMESTAMP"
mkdir -p "$REPORT_DIR"

echo "📁 Reports will be saved to: $REPORT_DIR"
echo ""

# 1. API Validator
echo -e "${YELLOW}[1/3] Running API Validator...${NC}"
python agents/api_validator.py \
    --backend api_server.py \
    --frontend trading_bot_interface.html \
    --fix \
    --output "$REPORT_DIR/api_validation.md"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ API validation complete${NC}"
else
    echo -e "${RED}✗ API validation failed${NC}"
fi
echo ""

# 2. Config Manager
echo -e "${YELLOW}[2/3] Running Config Manager...${NC}"
python agents/config_manager.py \
    --validate-all \
    --suggest-fixes \
    --output "$REPORT_DIR/config_audit.md"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Config validation complete${NC}"
else
    echo -e "${RED}✗ Config validation failed${NC}"
fi
echo ""

# 3. Code Reviewer (sample files)
echo -e "${YELLOW}[3/3] Running Code Reviewer on key files...${NC}"
KEY_FILES=("api_server.py" "main.py" "risk.py")

for file in "${KEY_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "  Reviewing $file..."
        python agents/code_reviewer.py \
            --file "$file" \
            --severity high \
            --output "$REPORT_DIR/review_$(basename $file .py).md" 2>/dev/null
    fi
done

echo -e "${GREEN}✓ Code reviews complete${NC}"
echo ""

# Summary
echo "========================================"
echo -e "${GREEN}✅ All validations complete!${NC}"
echo ""
echo "📊 Reports generated:"
ls -1 "$REPORT_DIR"
echo ""
echo "View reports:"
echo "  cd $REPORT_DIR && ls"
