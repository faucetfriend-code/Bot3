#!/bin/bash
# Generate tests for uncovered modules

set -e

echo "🧪 Trading Bot - Test Generator"
echo "========================================"
echo ""

# Modules that need tests (add more as needed)
MODULES=(
    "risk.py"
    "strategy.py"
    "execution.py"
    "market_data_feed.py"
    "ccxt_adapter.py"
)

# Create tests directory if it doesn't exist
mkdir -p tests

echo "Generating tests for ${#MODULES[@]} modules..."
echo ""

for module in "${MODULES[@]}"; do
    if [ -f "$module" ]; then
        test_file="tests/test_$(basename $module .py).py"

        if [ -f "$test_file" ]; then
            echo "⏭️  Skipping $module (test file already exists)"
        else
            echo "📝 Generating tests for $module..."
            python agents/test_generator.py \
                --module "$module" \
                --coverage-report \
                --output "$test_file"

            if [ $? -eq 0 ]; then
                echo "✓ Tests generated: $test_file"
            else
                echo "✗ Failed to generate tests for $module"
            fi
        fi
        echo ""
    else
        echo "⚠️  Module not found: $module"
        echo ""
    fi
done

echo "========================================"
echo "✅ Test generation complete!"
echo ""
echo "Run tests with:"
echo "  pytest tests/ -v"
echo ""
echo "Check coverage:"
echo "  pytest tests/ --cov=. --cov-report=html"
