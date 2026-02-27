<objective>
Analyze ALL documented bugs from bugs.md and missedbugs.md to create a comprehensive, dependency-aware fix plan that ensures no fixes will break existing functionality.

This is Stage 1 of a 3-stage bug fix workflow: Research → Implementation → Verification
</objective>

<context>
The trading bot has TWO bug catalogs:
1. **bugs.md** - 12 known issues (Critical: 2, High: 3, Medium: 3, Low: 4)
2. **missedbugs.md** - 35 undocumented issues (Critical: 9, High: 9, Medium: 16, Low: 16)

**Total: 47 bugs across the codebase**

The bugs have complex dependencies - fixing one may require fixing others first. Some fixes could introduce regression if not carefully planned.

Read both bug catalogs:
@bugs.md
@missedbugs.md

Read architectural context:
@CLAUDE.md
</context>

<analysis_requirements>
Perform deep analysis across these dimensions:

### 1. Dependency Mapping
For each bug, identify:
- **Blocks**: Which bugs must be fixed before this one?
- **Blocked by**: Which bugs are waiting on this fix?
- **Related**: Which bugs share the same root cause?
- **Conflicts**: Which fixes might interfere with each other?

Create a directed acyclic graph (DAG) of dependencies to determine fix order.

### 2. Risk Assessment
For each bug, evaluate:
- **Fix complexity**: Simple (< 10 lines), Moderate (10-50 lines), Complex (50+ lines or multi-file)
- **Regression risk**: Low (isolated change), Medium (affects multiple functions), High (architectural change)
- **Testing difficulty**: Can we verify the fix easily?
- **Rollback difficulty**: Can we undo if it breaks something?

### 3. Impact Analysis
For each proposed fix:
- What files will be modified?
- What functions/classes are affected?
- What other code depends on the modified code?
- Are there API contracts that could break?
- Will frontend need updates?
- Will database schema change?

### 4. Grouping and Batching
Group related bugs into logical batches:
- **Batch A**: Critical syntax/import errors (must fix first)
- **Batch B**: Database connection issues (enables data operations)
- **Batch C**: Security vulnerabilities (can be fixed in parallel)
- **Batch D**: Exception handling improvements (broad impact)
- **Batch E**: Code quality and cleanup (low risk)

Determine which batches can be done in parallel vs sequential.

### 5. Success Criteria
For each bug fix, define:
- How to verify the fix worked
- What tests to run
- What edge cases to check
- What NOT to break
</analysis_requirements>

<research>
### Phase 1: Catalog All Bugs
Read bugs.md and missedbugs.md completely. Create master list with:
- Bug ID (unique identifier)
- Title
- File(s) affected
- Line numbers
- Severity (Critical/High/Medium/Low)
- Category (Syntax/Logic/Security/etc.)
- Current status

### Phase 2: Understand Each Bug
For EVERY bug (all 47):
1. Read the actual source code at the specified lines
2. Understand the bug's root cause
3. Identify why it exists (incomplete refactor, rushed code, oversight)
4. Determine if the description is accurate
5. Check if it's already fixed (bugs.md says some are "Fixed")

### Phase 3: Map Dependencies
Create dependency graph:
```
Bug 1 (Missing Literal import)
  ↓ BLOCKS
Bug X (Mode switching feature)

Bug 2,3,4 (Hardcoded DB paths)
  ↓ BLOCKS
Bug 5 (Data not syncing)
  ↓ BLOCKS
Bug 6,7 (P&L calculations, funding rates)
```

### Phase 4: Identify Shared Root Causes
Group bugs with same underlying issue:
- All bare except clauses → Same pattern, single refactoring approach
- All hardcoded paths → Same constant usage fix
- All missing type hints → Systematic annotation pass

### Phase 5: Assess Risks
For each fix, identify potential side effects by:
- Grepping for all usages of modified code
- Reading functions that call the modified code
- Checking if there are tests for the modified code
- Reviewing git history for previous attempts to fix
</research>

<output_format>
Create a comprehensive fix plan saved to: `./bug-fix-plan.md`

Use this structure:

```markdown
# Comprehensive Bug Fix Plan

Generated: [timestamp]
Total Bugs: 47 (12 from bugs.md + 35 from missedbugs.md)
Estimated Fix Time: [X hours/days]

---

## Executive Summary

[3-4 paragraphs summarizing the fix strategy]

**Key Insights:**
- [Critical finding 1]
- [Critical finding 2]
- [Critical finding 3]

**Fix Approach:**
- [Overall strategy: sequential vs parallel, batch order, risk mitigation]

**Risk Mitigation:**
- [How we'll prevent regression]
- [How we'll verify each fix]
- [Rollback strategy if something breaks]

---

## Dependency Graph

```
[Visual representation of bug dependencies using ASCII art or hierarchical text]

Example:
Stage 1 (No dependencies - fix first):
├─ Bug #1: Missing Literal import
├─ Bug #2: Hardcoded DB path (add_new_tables.py)
├─ Bug #3: Hardcoded DB path (populate_market_history_sync.py)
└─ Bug #4: ConnectionPool lock type

Stage 2 (Depends on Stage 1):
├─ Bug #5: Infinite loop in connection pool
├─ Bug #6-13: All bare except clauses
└─ Bug #14: Duplicate Optional import

Stage 3 (Data layer working):
├─ Bug #15: P&L calculations
├─ Bug #16: Missing positions data
└─ Bug #17: Funding rates

...
```

---

## Fix Batches

### Batch A: Critical Syntax/Import Fixes (MUST FIX FIRST)
**Why first:** These prevent the server from starting at all
**Estimated time:** 30 minutes
**Regression risk:** Low (obvious syntax errors)
**Can run in parallel:** No (sequential)

#### Bug A1: Missing Literal Import
- **File:** api_server.py:14
- **Fix:** Add `Literal` to typing import
- **Lines changed:** 1
- **Dependencies:** None (standalone fix)
- **Verification:** Server starts without NameError
- **Risk:** None (just adding import)

**Before:**
```python
from typing import Dict, List, Optional, Any, Tuple
```

**After:**
```python
from typing import Dict, List, Optional, Any, Tuple, Literal
```

[Repeat for all bugs in Batch A]

---

### Batch B: Database Path Fixes (FIX SECOND)
**Why second:** Enables data operations to work correctly
**Estimated time:** 15 minutes
**Regression risk:** Medium (changes where data is stored)
**Can run in parallel:** Yes (different files)
**IMPORTANT:** Must verify database file location before and after

[Detail each bug fix]

---

### Batch C: Connection Pool Fixes (FIX THIRD)
**Why third:** Improves stability for subsequent fixes
**Estimated time:** 45 minutes
**Regression risk:** High (core infrastructure)
**Can run in parallel:** No (same file, interdependent)

[Detail each bug fix]

---

[Continue for all batches...]

---

## Detailed Fix Plans

### Bug #1: Missing Literal Import
**File:** `api_server.py`
**Lines:** 14, 514
**Severity:** Critical
**Category:** Import Error
**Estimated time:** 2 minutes

**Root Cause:**
Pydantic model uses Literal type annotation but it's not imported.

**Dependencies:**
- **Blocks:** Mode switching functionality
- **Blocked by:** None
- **Related:** Bug #8 (Duplicate Optional import) - both are import issues

**Fix Steps:**
1. Open api_server.py
2. Locate line 14: `from typing import Dict, List, Optional, Any, Tuple`
3. Add `Literal` to the import list
4. Verify ModeChangeRequest class (line 514) now has valid type annotation
5. Save file

**Verification:**
- [ ] Python can parse api_server.py without errors
- [ ] `python -c "from api_server import ModeChangeRequest"` succeeds
- [ ] Server starts without NameError
- [ ] Mode change endpoint returns valid response (not NameError)

**Regression Risks:**
- None (adding import has no side effects)

**Rollback:**
- Simply remove `Literal` from import if issues arise (unlikely)

**Files Modified:**
- api_server.py (1 line)

**Tests Needed:**
- Import test
- Mode switching API endpoint test

---

[Repeat detailed plan for EVERY bug - all 47]

---

## Risk Mitigation Strategy

### Before Making ANY Changes:
1. **Full backup:** Copy entire project directory
2. **Git commit:** Ensure working tree is clean, commit all current state
3. **Database backup:** Copy data/trading_bot.db to data/trading_bot.db.backup
4. **Document baseline:** Run server, capture all current errors/warnings
5. **Create test plan:** Document what currently works (don't break it)

### During Fixes:
1. **One batch at a time:** Don't jump ahead
2. **Commit after each batch:** Enables rollback to known-good state
3. **Test after each fix:** Don't accumulate untested changes
4. **Document anomalies:** If something unexpected happens, stop and investigate

### After Each Batch:
1. **Smoke tests:**
   - Does server start? `python api_server.py`
   - Does database open? Check for lock errors
   - Do basic API calls work? `curl http://localhost:8000/api/status`

2. **Regression tests:**
   - Compare new errors vs baseline
   - Ensure we didn't introduce NEW bugs
   - Verify existing functionality still works

3. **Git commit:**
   - Descriptive commit message
   - Reference which bugs were fixed
   - Example: `fix: critical import errors (bugs #1, #8, #14)`

### If Something Breaks:
1. **Stop immediately** - don't make more changes
2. **Identify what broke** - compare to baseline
3. **Check git diff** - review what changed
4. **Rollback if needed** - `git reset --hard HEAD~1`
5. **Re-plan the fix** - understand why it broke
6. **Try again with different approach**

---

## Testing Strategy

### Test Levels:

**Level 1: Syntax Tests**
- Python can parse all .py files without SyntaxError
- All imports resolve
- Run: `python -m py_compile *.py`

**Level 2: Server Start Test**
- API server starts without errors
- No immediate crashes
- Run: `python api_server.py` (should start successfully)

**Level 3: API Connectivity Tests**
- GET /api/status returns valid JSON
- GET /api/positions doesn't crash
- GET /api/market-data returns data
- Run: `curl -X GET http://localhost:8000/api/status`

**Level 4: Database Tests**
- Database file exists at data/trading_bot.db
- Tables exist and are accessible
- No lock errors during concurrent access
- Run: `sqlite3 data/trading_bot.db "SELECT name FROM sqlite_master WHERE type='table';"`

**Level 5: Functional Tests**
- Mode switching works
- Position data displays correctly
- P&L calculations are accurate
- Funding rates load
- (Manual testing via UI)

### Test After Each Batch:
- Run ALL levels appropriate for that batch
- Don't proceed to next batch if any test fails
- Document test results

---

## Success Criteria

This plan is successful when:

1. ✅ All 47 bugs have detailed fix plans
2. ✅ Dependencies are mapped and fix order is clear
3. ✅ Risk assessment completed for every fix
4. ✅ Verification steps defined for every fix
5. ✅ Batch order determined with parallel opportunities identified
6. ✅ Risk mitigation strategy is comprehensive
7. ✅ Testing strategy covers all critical paths
8. ✅ Rollback strategy is clear
9. ✅ Estimated time is realistic
10. ✅ The plan answers: "Can we fix everything without breaking anything?"

---

## Estimated Timeline

**Batch A (Critical Syntax):** 30 minutes
**Batch B (Database Paths):** 15 minutes + 15 minutes testing
**Batch C (Connection Pool):** 45 minutes + 30 minutes testing
**Batch D (Exception Handling):** 2 hours + 30 minutes testing
**Batch E (Security):** 1 hour + 30 minutes testing
**Batch F (Code Quality):** 3 hours + 1 hour testing

**Total estimated time:** 8-10 hours of focused work

**Recommended schedule:**
- Day 1 (3 hours): Batches A, B, C - Critical fixes
- Day 2 (3 hours): Batch D - Exception handling
- Day 3 (4 hours): Batches E, F - Security and quality

---

## Notes

- This is a PLAN document, not implementation
- Implementation will happen in Stage 2 (separate prompt)
- Verification will happen in Stage 3 (separate prompt)
- Do NOT make code changes while creating this plan
- Focus on understanding, not fixing
```
</output_format>

<verification>
Before declaring the analysis complete, verify:

1. **Completeness:**
   - All 47 bugs from both files are analyzed
   - Every bug has a detailed fix plan
   - No bugs are skipped or missed

2. **Dependency Accuracy:**
   - Dependency graph is logically sound
   - No circular dependencies
   - Fix order makes sense

3. **Risk Assessment Realism:**
   - Risks are honestly evaluated
   - Not downplaying dangers
   - Mitigation strategies are practical

4. **Testability:**
   - Every fix has verification steps
   - Tests are actually executable
   - Success criteria are measurable

5. **Actionability:**
   - Next steps are clear
   - Implementation prompt can use this directly
   - Nothing is vague or hand-wavy
</verification>

<success_criteria>
The analysis is successful when:

1. ✅ bug-fix-plan.md file exists with comprehensive analysis
2. ✅ All 47 bugs documented with detailed fix plans
3. ✅ Dependency graph shows fix order clearly
4. ✅ Risk mitigation strategy is thorough
5. ✅ Testing strategy covers all scenarios
6. ✅ Timeline is realistic and actionable
7. ✅ The plan provides 100% confidence we can fix everything safely
8. ✅ The plan answers: "What's the smartest way to fix all bugs without introducing new ones?"
</success_criteria>

<constraints>
- Do NOT make any code changes - this is analysis/planning only
- Do NOT skip bugs - analyze all 47
- Do NOT guess - read actual source code to understand each bug
- Do NOT oversimplify - acknowledge real complexity and risks
- Do NOT create overly optimistic timeline - be realistic
- Focus on THOROUGHNESS over speed - this plan guides expensive implementation work
</constraints>

<parallel_tool_execution>
For maximum efficiency:
- Read multiple source files in parallel to understand bugs
- Run multiple grep searches in parallel to find dependencies
- Use glob patterns in parallel to discover affected files
</parallel_tool_execution>

<reflection_guidance>
After analyzing each bug:
1. Verify you understand the root cause (not just symptoms)
2. Check if proposed fix could break something else
3. Consider alternative fix approaches
4. Assess if the fix is actually necessary (some may be opinion-based)
5. Prioritize fixes that unblock other fixes
</reflection_guidance>
