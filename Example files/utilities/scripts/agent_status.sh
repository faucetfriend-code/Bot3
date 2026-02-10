#!/bin/bash
# Check agent system status and health

echo "🤖 Trading Bot Agent System Status"
echo "========================================"
echo ""

# Check Python version
echo "📍 Python Environment:"
python --version
echo ""

# Check if required packages are installed
echo "📦 Required Packages:"
packages=("langchain" "langchain-anthropic" "anthropic")
all_installed=true

for pkg in "${packages[@]}"; do
    if python -c "import $pkg" 2>/dev/null; then
        version=$(python -c "import $pkg; print($pkg.__version__)" 2>/dev/null || echo "unknown")
        echo "  ✓ $pkg ($version)"
    else
        echo "  ✗ $pkg (not installed)"
        all_installed=false
    fi
done
echo ""

# Check API key
echo "🔑 API Key Status:"
if [ -f ".env" ]; then
    if grep -q "ANTHROPIC_API_KEY=" .env; then
        key=$(grep "ANTHROPIC_API_KEY=" .env | cut -d= -f2)
        if [ -n "$key" ] && [ "$key" != "your-anthropic-api-key-here" ]; then
            echo "  ✓ API key configured"
        else
            echo "  ✗ API key not set in .env"
        fi
    else
        echo "  ✗ ANTHROPIC_API_KEY not found in .env"
    fi
else
    echo "  ✗ .env file not found"
fi
echo ""

# Check agent files
echo "🎯 Agent Files:"
agents=("api_validator.py" "code_reviewer.py" "code_writer.py" "test_generator.py" "config_manager.py")

for agent in "${agents[@]}"; do
    if [ -f "agents/$agent" ]; then
        echo "  ✓ $agent"
    else
        echo "  ✗ $agent (missing)"
    fi
done
echo ""

# Overall status
echo "========================================"
if $all_installed && [ -f ".env" ]; then
    echo "✅ Agent system ready!"
    echo ""
    echo "Usage examples:"
    echo "  python agents/api_validator.py --backend api_server.py --frontend trading_bot_interface.html"
    echo "  python agents/code_reviewer.py --file api_server.py --output review.md"
    echo "  python agents/test_generator.py --module risk.py --output tests/test_risk.py"
else
    echo "⚠️  Agent system needs configuration"
    echo ""
    if ! $all_installed; then
        echo "Install dependencies:"
        echo "  pip install -r requirements.txt"
    fi
    if [ ! -f ".env" ]; then
        echo "Create .env file:"
        echo "  cp .env.example .env"
        echo "  # Add your ANTHROPIC_API_KEY to .env"
    fi
fi
