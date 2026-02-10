#!/bin/bash
# Development Environment Setup Script
# Sets up pre-commit hooks and development safeguards
# Cross-platform compatible (Linux/macOS/Windows with Git Bash)

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Detect OS
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]]; then
    IS_WINDOWS=true
    PYTHON_CMD="python"
else
    IS_WINDOWS=false
    PYTHON_CMD="python3"
fi

echo -e "${BLUE}🚀 Setting up Trading Bot Development Environment${NC}"
echo "=================================================="

# Check if we're in the right directory
if [[ ! -f "api_server.py" ]] || [[ ! -f "pyproject.toml" ]]; then
    echo -e "${RED}❌ Error: Please run this script from the trading bot root directory${NC}"
    exit 1
fi

echo -e "${YELLOW}📦 Installing Python dependencies...${NC}"
if ! $PYTHON_CMD -m pip install --upgrade pip; then
    echo -e "${RED}❌ Failed to upgrade pip${NC}"
    exit 1
fi

# Install development dependencies
if ! $PYTHON_CMD -m pip install pre-commit black isort flake8 mypy bandit safety detect-secrets pytest pytest-cov; then
    echo -e "${RED}❌ Failed to install development tools${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Development tools installed${NC}"

# Install pre-commit hooks
echo -e "${YELLOW}🔗 Installing pre-commit hooks...${NC}"
if ! pre-commit install; then
    echo -e "${RED}❌ Failed to install pre-commit hooks${NC}"
    exit 1
fi

# Also install pre-push hook for full test suite
if ! pre-commit install --hook-type pre-push; then
    echo -e "${YELLOW}⚠️  Warning: Could not install pre-push hooks (may not be supported in this git version)${NC}"
fi

echo -e "${GREEN}✅ Pre-commit hooks installed${NC}"

# Create secrets baseline for detect-secrets
echo -e "${YELLOW}🔒 Setting up secrets detection...${NC}"
if [[ ! -f ".secrets.baseline" ]]; then
    if detect-secrets scan --update .secrets.baseline; then
        echo -e "${GREEN}✅ Secrets baseline created${NC}"
    else
        echo -e "${YELLOW}⚠️  Warning: Could not create secrets baseline${NC}"
    fi
else
    echo -e "${BLUE}ℹ️  Secrets baseline already exists${NC}"
fi

# Run initial pre-commit check
echo -e "${YELLOW}🔍 Running initial code quality check...${NC}"
if pre-commit run --all-files; then
    echo -e "${GREEN}✅ All checks passed!${NC}"
else
    echo -e "${YELLOW}⚠️  Some checks failed. Please fix the issues and try again.${NC}"
    echo -e "${BLUE}💡 Tip: Run 'pre-commit run --all-files' to see detailed errors${NC}"
fi

# Create development configuration
echo -e "${YELLOW}⚙️  Setting up development configuration...${NC}"

# Create .env.development if it doesn't exist
if [[ ! -f ".env.development" ]]; then
    cat > .env.development << 'EOF'
# Development Environment Configuration
# Copy this to .env for local development

# Testnet mode (recommended for development)
TESTNET=true

# Mock external APIs during development
MOCK_TRADING=true
DISABLE_EXTERNAL_APIS=true

# Development database
DATABASE_PATH=data/trading_bot_dev.db

# Logging
LOG_LEVEL=DEBUG

# Development server
DEV_MODE=true
AUTO_RELOAD=true

# Test configuration
PYTEST_DISABLE_PLUGIN_AUTOLOAD=true
PYTEST_ADDOPTS=--strict-markers --disable-warnings

# Pre-commit and linting
SKIP_PRE_COMMIT=false
EOF
    echo -e "${GREEN}✅ Created .env.development${NC}"
else
    echo -e "${BLUE}ℹ️  .env.development already exists${NC}"
fi

# Create scripts directory if it doesn't exist
mkdir -p scripts

# Create development server script
cat > scripts/dev-server.sh << 'EOF'
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

# Check if critical tests pass
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
EOF

chmod +x scripts/dev-server.sh
echo -e "${GREEN}✅ Created scripts/dev-server.sh${NC}"

# Create check-ready-for-commit script
cat > scripts/check-ready-for-commit.sh << 'EOF'
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
EOF

chmod +x scripts/check-ready-for-commit.sh
echo -e "${GREEN}✅ Created scripts/check-ready-for-commit.sh${NC}"

# Create critical tests runner
cat > scripts/run-critical-tests.sh << 'EOF'
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
EOF

chmod +x scripts/run-critical-tests.sh
echo -e "${GREEN}✅ Created scripts/run-critical-tests.sh${NC}"

# Create parallel test runner
cat > scripts/test-parallel.sh << 'EOF'
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
EOF

chmod +x scripts/test-parallel.sh
echo -e "${GREEN}✅ Created scripts/test-parallel.sh${NC}"

# Update Makefile with new targets
if [[ -f "Makefile" ]]; then
    echo -e "${YELLOW}📝 Updating Makefile with new targets...${NC}"

    # Add new targets to Makefile
    cat >> Makefile << 'EOF'

# Development safeguards
setup-dev: ## Set up development environment with pre-commit hooks
	./scripts/setup-dev-environment.sh

dev-server: ## Start development server with safeguards
	./scripts/dev-server.sh

check-ready: ## Check if code is ready for commit
	./scripts/check-ready-for-commit.sh

critical-tests: ## Run critical path tests only
	./scripts/run-critical-tests.sh

test-parallel: ## Run tests in parallel
	./scripts/test-parallel.sh

# Pre-commit hooks
pre-commit-install: ## Install pre-commit hooks
	pre-commit install

pre-commit-run: ## Run pre-commit on all files
	pre-commit run --all-files

pre-commit-update: ## Update pre-commit hooks
	pre-commit autoupdate
EOF

    echo -e "${GREEN}✅ Updated Makefile${NC}"
fi

echo ""
echo -e "${GREEN}🎉 Development environment setup complete!${NC}"
echo ""
echo -e "${BLUE}📋 Next steps:${NC}"
echo "  1. Copy .env.development to .env for local development"
echo "  2. Run 'make dev-server' to start the development server"
echo "  3. Run 'make check-ready' before committing changes"
echo "  4. Use 'make critical-tests' for fast feedback during development"
echo ""
echo -e "${BLUE}🔧 Available commands:${NC}"
echo "  make setup-dev      - Re-run this setup"
echo "  make dev-server     - Start development server with safeguards"
echo "  make check-ready    - Verify code is ready for commit"
echo "  make critical-tests - Run critical tests only"
echo "  make test-parallel  - Run all tests in parallel"
echo ""
echo -e "${YELLOW}⚠️  Remember: Pre-commit hooks will run automatically on git commit!${NC}"