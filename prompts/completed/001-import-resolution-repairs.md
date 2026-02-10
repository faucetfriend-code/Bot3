<objective>
Fix import resolution issues in the trading bot project to ensure LSP compatibility while maintaining runtime functionality. Use the guidelines from import fix.txt to refactor dynamic imports to absolute imports and configure LSP properly.
</objective>

<context>
Project: Trading bot with core_logic package and trading_bot_v2 application.
Issues: LSP cannot resolve imports due to sys.path.insert() and bare imports, as detailed in import-resolution-analysis.md.
Fixes: Use absolute imports, configure LSP workspace settings, as mandated by import fix.txt.
Files to examine: @research/import fix.txt, @research/import-resolution-analysis.md
Who will use: Developers working on the trading bot, to eliminate false positive LSP errors.
What it's for: Improve development experience by aligning static analysis with runtime behavior.
</context>

<requirements>
Thoroughly analyze the codebase to identify all import-related issues:
1. Find all files using sys.path.insert() for import path manipulation
2. Locate all bare imports (e.g., from models import Signal) that should be absolute
3. Verify core_logic is properly structured as a Python package
4. Identify any LSP configuration files that need updating

Apply repairs following import fix.txt rules:
1. Replace all sys.path.insert() calls with proper package structure
2. Convert bare imports to absolute imports (e.g., from core_logic.models import Signal)
3. Ensure core_logic/ contains __init__.py and is importable from project root
4. Create/update pyrightconfig.json with proper extraPaths and include settings
5. Update VS Code settings.json if needed for LSP configuration

Go beyond basics to ensure complete LSP compatibility - every import must be resolvable statically.
</requirements>

<implementation>
Follow these mandatory rules from import fix.txt:
- Use absolute package imports only (from core_logic.module import ...)
- Treat core_logic as a real package (must have __init__.py, be importable without hacks)
- Configure LSP explicitly (extraPaths, include settings)
- Remove all sys.path.insert() from application code
- No bare module imports from path hacks
- LSP errors are build failures - fix structure, not suppress

For complex refactoring, consider multiple approaches and choose the most maintainable solution.
</implementation>

<output>
Create/modify files with relative paths:
- ./pyrightconfig.json - LSP configuration with extraPaths including ./core_logic
- ./trading_bot_v2/trading_bot.py - Refactor imports to absolute
- Any other files with import issues - Apply same refactoring
- If needed, update .vscode/settings.json for workspace configuration

Save analysis of changes to: ./research/import-repair-summary.md
</output>

<verification>
Before declaring complete, verify thoroughly:
- Run LSP analysis (Pylance/Pyright) to confirm zero import resolution errors
- Execute the application to ensure runtime functionality is preserved
- Test that all imports work both statically and at runtime
- Check that no sys.path manipulation remains in application code
</verification>

<success_criteria>
- All imports resolve correctly in LSP without any errors
- No sys.path.insert() calls in application code
- Runtime execution works perfectly
- LSP and runtime environments are aligned
- Code follows absolute import patterns exclusively
</success_criteria>

---
Completed at: 2026-01-14T06:04:32.420Z
