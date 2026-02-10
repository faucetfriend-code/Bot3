# Import Resolution Problem Analysis: LSP vs Runtime Environment Mismatch

## Problem Summary

The trading bot project encounters a critical mismatch between **static analysis (LSP)** and **runtime execution** environments. While the code functions perfectly at runtime, the Language Server Protocol (LSP) cannot resolve imports, leading to false positive errors that obscure real issues.

## Technical Context

### Project Structure
```
trading_bot_project/
├── core_logic/           # Shared modules package
│   ├── __init__.py
│   ├── models.py
│   ├── indicators.py
│   └── pacifica_client.py
├── trading_bot_v2/       # Main application
│   ├── trading_bot.py    # Main bot logic
│   ├── strategy_manager.py
│   └── [other modules]
└── Example files/        # Legacy location
    └── core_logic/       # Original module location
```

### Import Strategy Used
The application uses dynamic path manipulation for imports:

```python
# In trading_bot_v2/trading_bot.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core_logic"))
from models import Signal, OrderSide
from indicators import calculate_adx, calculate_atr, calculate_bollinger_bands
```

## Root Cause Analysis

### 1. Static vs Dynamic Analysis

**LSP (Static Analysis)**:
- Analyzes code without execution
- Cannot predict runtime path modifications
- Requires all imports to be resolvable at analysis time
- Uses configured Python path and workspace settings

**Python Runtime (Dynamic Analysis)**:
- Executes code sequentially
- Can modify `sys.path` at runtime
- Resolves imports based on actual execution environment
- Adapts to dynamic path changes

### 2. Import Resolution Mechanism

**LSP Resolution Process**:
```
1. Check current file's directory
2. Check configured workspace folders
3. Check PYTHONPATH environment variable
4. Check installed packages
5. Fail if not found
```

**Runtime Resolution Process**:
```
1. Check sys.path (modified at runtime)
2. Check current file's directory
3. Check PYTHONPATH
4. Check installed packages
5. Execute import
```

### 3. The Critical Difference

The LSP cannot "see" the `sys.path.insert()` call because:
- It analyzes files in isolation
- It doesn't execute the code
- It uses a static Python path configuration
- Dynamic modifications happen only at runtime

## Specific Issues Encountered

### Import Errors
```
ERROR [23:6] Import "models" could not be resolved
ERROR [24:6] Import "indicators" could not be resolved
```

### Attribute Access Errors
```
ERROR [197:28] Cannot access attribute "get_ticker" for class "PacificaClient"
```

### Type Resolution Errors
```
ERROR [242:44] Expected class but received "(iterable: Iterable[object], /) -> bool"
```

## Why This Problem Occurs

### 1. LSP Design Limitations
- LSP is designed for static analysis, not dynamic execution
- Cannot handle runtime path modifications
- Requires explicit configuration for non-standard import paths

### 2. Python Import System
- Python's import system is highly dynamic
- `sys.path` can be modified at runtime
- Relative imports and path manipulation are valid Python patterns
- LSP tools struggle with these dynamic patterns

### 3. Development Environment Mismatch
- Runtime environment has full access to file system
- LSP environment may be sandboxed or have different path configurations
- Workspace settings may not include all necessary paths

## Potential Solutions to Research

### 1. LSP Workspace Configuration

**VS Code Settings (settings.json)**:
```json
{
    "python.analysis.extraPaths": [
        "${workspaceFolder}/core_logic",
        "${workspaceFolder}/Example files/core_logic"
    ],
    "python.analysis.include": [
        "${workspaceFolder}"
    ]
}
```

**Pyright/Pylance Configuration (pyrightconfig.json)**:
```json
{
    "include": ["."],
    "extraPaths": ["./core_logic", "./Example files/core_logic"],
    "pythonPath": "/path/to/python",
    "venvPath": "."
}
```

### 2. PYTHONPATH Environment Configuration

**System Level**:
```bash
export PYTHONPATH="${PYTHONPATH}:/path/to/trading_bot_project/core_logic"
```

**IDE Level**:
- Configure Python interpreter with custom PYTHONPATH
- Add virtual environment with proper path configuration

### 3. Import System Refactoring

**Option A: Absolute Imports**
```python
from core_logic.models import Signal, OrderSide
from core_logic.indicators import calculate_adx
```

**Option B: Relative Imports**
```python
from ..core_logic.models import Signal, OrderSide
```

**Option C: Package Structure**
```
trading_bot_project/
├── src/
│   ├── core_logic/
│   └── trading_bot_v2/
├── setup.py
└── pyproject.toml
```

### 4. LSP-Specific Solutions

**Stub Files (.pyi)**:
```python
# core_logic/models.pyi
from typing import Dict, Any
class Signal:
    def __init__(self, **kwargs): ...
class OrderSide: ...
```

**Type Checking Configuration**:
```python
# In code
import sys
if sys.version_info >= (3, 8):
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from core_logic.models import Signal
```

### 5. Alternative LSP Tools

**Research Options**:
- Pyright (Microsoft's Python LSP) - more configurable
- Pylsp (Python LSP Server) - highly extensible
- Ruff - fast linter with import resolution
- MyPy with custom configuration

### 6. Development Environment Solutions

**Virtual Environment Configuration**:
```bash
python -m venv venv
source venv/bin/activate
pip install -e .
# Configure LSP to use virtual environment
```

**IDE-Specific Solutions**:
- VS Code: Configure workspace settings
- PyCharm: Configure project structure and Python path
- Vim/Neovim: Configure LSP server settings

## Workarounds Currently Implemented

### 1. Dynamic Path Manipulation
```python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core_logic"))
```

### 2. Package Structure
- Created `core_logic/` as proper Python package
- Added `__init__.py` files
- Configured `setup.py` and `pyproject.toml`

### 3. Type Stub Attempts
- Created `.pyi` files for type hints
- Used `TYPE_CHECKING` blocks for conditional imports

## Research Directions

### 1. LSP Configuration Research
- Investigate LSP server configuration options
- Test different LSP implementations (Pyright, Pylsp, etc.)
- Compare behavior across different editors/IDEs

### 2. Python Import System Research
- Study Python's import system documentation
- Research best practices for large Python projects
- Investigate modern Python packaging standards

### 3. Development Environment Research
- Compare LSP behavior in different environments
- Test with different Python versions
- Evaluate containerized development environments

### 4. Alternative Architectures
- Research monorepo vs multi-package structures
- Investigate namespace packages
- Study large-scale Python project organization

## Recommended Research Approach

1. **Start with LSP Configuration**: Try configuring your LSP workspace settings first
2. **Test Different Tools**: Compare Pyright, Pylsp, and other LSP implementations
3. **Environment Setup**: Ensure your development environment matches runtime
4. **Gradual Refactoring**: Consider migrating to absolute imports over time
5. **Community Resources**: Check Python LSP communities and documentation

## Expected Outcomes

**Successful Resolution**:
- LSP can resolve all imports correctly
- Static analysis matches runtime behavior
- Development experience improves significantly
- False positive errors are eliminated

**Partial Resolution**:
- LSP configuration allows most imports to resolve
- Remaining issues are minimized and documented
- Development workflow is acceptable

**Acceptable Workaround**:
- Runtime functionality remains perfect
- LSP limitations are well-understood
- Development continues with awareness of limitations

This analysis provides a comprehensive foundation for researching and implementing a solution to the import resolution mismatch between LSP static analysis and Python runtime execution.