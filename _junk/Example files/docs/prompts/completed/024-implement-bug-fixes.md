<objective>
Execute the comprehensive bug fix plan from bug-fix-plan.md to systematically fix all 47 documented bugs without introducing regression or breaking existing functionality.

This is Stage 2 of a 3-stage bug fix workflow: Research → **Implementation** → Verification
</objective>

<context>
Stage 1 (Research) has produced a detailed fix plan in bug-fix-plan.md that includes:
- All 47 bugs cataloged with detailed analysis
- Dependency graph showing fix order
- Risk assessment for each fix
- Batch groupings for logical fix order
- Verification steps for each fix
- Comprehensive risk mitigation strategy

Now we execute the plan methodically, one batch at a time, with continuous testing.

Read the fix plan:
@bug-fix-plan.md

Read bug catalogs for reference:
@bugs.md
@missedbugs.md

Read architectural context:
@CLAUDE.md
</context>

<implementation_requirements>
### Core Principles:

1. **Follow the Plan Exactly**
   - Execute batches in the order specified in bug-fix-plan.md
   - Don't skip ahead or reorder without justification
   - If plan needs adjustment, document why

2. **One Batch at a Time**
   - Complete entire batch before moving to next
   - Test after each batch
   - Commit after each successful batch
   - Don't accumulate untested changes

3. **Continuous Verification**
   - Run verification steps after EVERY fix
   - Don't assume a fix worked - test it
   - If verification fails, investigate before proceeding
   - Document any unexpected behavior

4. **Safe Modification Practices**
   - Read the file completely before editing
   - Understand surrounding code context
   - Make minimal changes (exactly what's needed, nothing more)
   - Preserve existing formatting and style
   - Don't refactor while fixing bugs (tempting but risky)

5. **Immediate Rollback on Failure**
   - If a fix breaks something, rollback immediately (git reset)
   - Investigate what went wrong
   - Revise the fix approach
   - Try again with more caution

### Workflow for Each Batch:

**Before Starting Batch:**
1. Read the batch description from bug-fix-plan.md
2. Understand which bugs are in this batch and why they're grouped
3. Review dependencies - are prerequisites met?
4. Check risk assessment - what could go wrong?
5. Prepare verification tests

**During Batch Execution:**
1. For each bug in the batch:
   a. Read the detailed fix plan
   b. Read the source file(s) completely
   c. Understand the current code
   d. Make the specified changes using Edit tool
   e. Verify the changes are correct (re-read modified sections)
   f. Run syntax check: `python -m py_compile [file]`

**After Batch Completion:**
1. Run batch-level verification tests
2. Test that server starts: `python api_server.py` (kill after startup)
3. Run smoke tests (if applicable)
4. Check for new errors or warnings
5. Compare behavior to baseline
6. Commit changes: `git add [files] && git commit -m "fix: [description]"`
7. Document any issues or surprises
8. Take a breath, review what was done
9. Proceed to next batch

### Error Handling:

**If Syntax Error After Fix:**
- Re-read the modified code
- Check for typos, missing commas, unbalanced brackets
- Verify indentation is correct
- Use git diff to see exactly what changed
- Fix the syntax error before proceeding

**If Server Won't Start After Fix:**
- Read the error traceback carefully
- Identify which fix caused the issue
- Check if a dependency was missed
- Consider if the fix was incomplete
- Rollback and revise approach

**If Test Fails After Fix:**
- Understand what the test is checking
- Verify the fix actually addressed the root cause
- Check if the fix had unintended side effects
- Look for usage in other files
- Consider partial rollback or additional fixes

### Documentation:

**During Implementation:**
- Keep notes on any deviations from plan
- Document surprises or unexpected findings
- Record any additional bugs discovered
- Note which fixes were easier/harder than expected

**After Implementation:**
- Update bugs.md to mark fixed issues as "Fixed"
- Document what was actually changed
- Note any remaining issues
- Provide summary for Stage 3 verification
</implementation_requirements>

<execution_plan>
Execute the batches in order specified by bug-fix-plan.md:

### Batch A: Critical Syntax/Import Fixes
Execute first (highest priority, enables server startup)

For each bug in Batch A:
1. Read api_server.py completely
2. Locate the specific line number
3. Apply the fix using Edit tool
4. Verify with: `python -m py_compile api_server.py`
5. Continue to next bug in batch

After Batch A complete:
- Test server startup: `python api_server.py` (should start without NameError)
- Commit: `git add api_server.py && git commit -m "fix: critical import errors (Batch A)"`

### Batch B: Database Path Fixes
Execute second (enables data operations)

For each bug in Batch B:
1. Read the affected file completely
2. Locate hardcoded "trading_bot.db" strings
3. Replace with proper `DATABASE_PATH` import and usage
4. Verify imports are correct
5. Test with: `python -m py_compile [file]`

After Batch B complete:
- Verify database path: `python -c "from database import DATABASE_PATH; print(DATABASE_PATH)"`
- Check only one database file exists: `find . -name "trading_bot.db"`
- Commit: `git add [files] && git commit -m "fix: hardcoded database paths (Batch B)"`

### Batch C: Connection Pool Fixes
Execute third (improves stability)

### Batch D: Exception Handling Fixes
Execute fourth (all bare except clauses)

### Batch E: Security Fixes
Execute fifth (can be parallel with D if careful)

### Batch F: Code Quality Fixes
Execute last (lowest risk, highest volume)

[Follow plan for exact batch contents and order]
</execution_plan>

<output_format>
Create an implementation report saved to: `./bug-fix-implementation-report.md`

Use this structure:

```markdown
# Bug Fix Implementation Report

Generated: [timestamp]
Bugs Fixed: [X of 47]
Batches Completed: [X]
Total Time: [X hours]
Status: [In Progress / Complete / Blocked]

---

## Executive Summary

[2-3 paragraphs summarizing what was accomplished]

**Successes:**
- [Major accomplishment 1]
- [Major accomplishment 2]

**Challenges:**
- [Unexpected issue 1 and how it was handled]
- [Unexpected issue 2 and how it was handled]

**Remaining Work:**
- [What's left to do, if anything]

---

## Batch-by-Batch Summary

### Batch A: Critical Syntax/Import Fixes
**Status:** ✅ Complete
**Time:** 25 minutes
**Bugs Fixed:** #1, #8, #14
**Commits:** fix: critical import errors (abc123)

**What Was Done:**
- Added Literal to typing import in api_server.py:14
- Removed duplicate Optional import from api_server.py:28
- [Other fixes in this batch]

**Verification Results:**
✅ Server starts without NameError
✅ ModeChangeRequest class validates correctly
✅ No new errors introduced

**Issues Encountered:**
None - batch went smoothly as planned

**Files Modified:**
- api_server.py (2 lines changed)

---

### Batch B: Database Path Fixes
**Status:** ✅ Complete
**Time:** 30 minutes
**Bugs Fixed:** #2, #3, #27
**Commits:** fix: hardcoded database paths (def456)

**What Was Done:**
- Updated add_new_tables.py to use DATABASE_PATH
- Updated populate_market_history_sync.py to use DATABASE_PATH
- Verified all modules use consistent database location

**Verification Results:**
✅ Only one trading_bot.db file exists (in data/ directory)
✅ Scripts write to correct database
✅ Data persists between server restarts

**Issues Encountered:**
- Found an additional hardcoded path in [file] not in original bug list
- Added to fix and documented for verification stage

**Files Modified:**
- add_new_tables.py (1 line + 1 import)
- populate_market_history_sync.py (1 line + 1 import)

---

[Continue for all batches...]

---

## Detailed Fix Log

### Bug #1: Missing Literal Import
**Status:** ✅ Fixed
**Time:** 3 minutes
**Batch:** A

**Changes Made:**
```python
# Before (api_server.py:14)
from typing import Dict, List, Optional, Any, Tuple

# After (api_server.py:14)
from typing import Dict, List, Optional, Any, Tuple, Literal
```

**Verification:**
- ✅ `python -m py_compile api_server.py` succeeds
- ✅ Server starts without NameError
- ✅ ModeChangeRequest class is valid

**Side Effects:**
None observed

---

[Repeat for EVERY bug fixed - all 47]

---

## Commits Made

1. **fix: critical import errors (Batch A)**
   - Commit: abc123
   - Files: api_server.py
   - Bugs: #1, #8, #14
   - Time: 2024-12-03 12:30

2. **fix: hardcoded database paths (Batch B)**
   - Commit: def456
   - Files: add_new_tables.py, populate_market_history_sync.py
   - Bugs: #2, #3
   - Time: 2024-12-03 13:00

[All commits...]

---

## Verification Test Results

### Syntax Tests (Level 1):
✅ All Python files parse without SyntaxError
✅ All imports resolve
```bash
python -m py_compile *.py
# Output: No errors
```

### Server Start Test (Level 2):
✅ API server starts successfully
```bash
python api_server.py
# Output: Uvicorn running on http://0.0.0.0:8000
```

### API Connectivity Tests (Level 3):
✅ GET /api/status returns valid JSON
✅ GET /api/positions doesn't crash
✅ GET /api/market-data returns data
```bash
curl http://localhost:8000/api/status
# Output: {"success": true, ...}
```

### Database Tests (Level 4):
✅ Database exists at data/trading_bot.db
✅ Tables are accessible
✅ No lock errors
```bash
sqlite3 data/trading_bot.db "SELECT name FROM sqlite_master WHERE type='table';"
# Output: [list of tables]
```

### Functional Tests (Level 5):
✅ Mode switching works
✅ Position data displays correctly
⚠️  P&L calculations need verification (Stage 3)
⚠️  Funding rates need verification (Stage 3)

---

## Issues Discovered During Implementation

### Additional Bugs Found:
1. **Undocumented hardcoded path in [file]**
   - Discovered while fixing Batch B
   - Fixed in same commit
   - Not in original bug catalogs

2. **Import statement formatting inconsistency**
   - Some files use absolute imports, others relative
   - Not a bug but noted for future cleanup

### Plan Deviations:
1. **Batch C took longer than estimated**
   - Original estimate: 45 minutes
   - Actual time: 75 minutes
   - Reason: ConnectionPool fix more complex than anticipated

2. **Batch D and E combined**
   - Originally separate batches
   - Combined because fixes didn't conflict
   - Saved time overall

### Lessons Learned:
- Verification after each fix caught 3 issues before they accumulated
- Reading entire file before editing prevented context mistakes
- Small commits made it easy to review changes
- Testing continuously gave confidence to proceed

---

## Files Modified Summary

| File | Lines Changed | Bugs Fixed | Batch |
|------|---------------|------------|-------|
| api_server.py | 25 | #1,#6-13,#14,#15,#17 | A,D,E |
| database.py | 12 | #4,#5,#12,#19 | C |
| add_new_tables.py | 2 | #2 | B |
| populate_market_history_sync.py | 2 | #3 | B |
| agent/base_agent.py | 3 | #7 | D |
| market_data_feed.py | 3 | #7 | D |
| process_manager.py | 6 | #7 | D |
| risk_advanced.py | 9 | #7 | D |
| [other files...] | ... | ... | ... |

**Total files modified:** [X]
**Total lines changed:** [Y]
**Total bugs fixed:** [Z of 47]

---

## Remaining Work

### Bugs Not Yet Fixed:
[List any bugs that weren't fixed and why]

### Blockers:
[Any issues preventing completion]

### Next Steps:
1. [What needs to happen next]
2. [Stage 3: Comprehensive verification]

---

## Recommendations for Stage 3 (Verification)

Focus verification on:
1. **P&L calculations** - Most critical for financial accuracy
2. **Database data integrity** - Ensure no data corruption from path fixes
3. **Exception handling** - Verify errors are logged properly now
4. **Security** - Test CORS, rate limiting, input validation
5. **Regression testing** - Ensure nothing broke that was working

Test scenarios:
- [Specific test case 1]
- [Specific test case 2]
- [etc.]

---

## Success Metrics

✅ 47/47 bugs fixed
✅ 0 new bugs introduced
✅ All verification tests pass
✅ Server runs stably
✅ Git history is clean with logical commits
✅ Implementation report is comprehensive

---

## Notes

[Any additional context, observations, or recommendations]
```
</output_format>

<verification>
Before declaring implementation complete, verify:

1. **Completeness:**
   - All batches from bug-fix-plan.md executed
   - All 47 bugs addressed
   - No bugs skipped without justification

2. **Code Quality:**
   - All files parse without syntax errors
   - No new lint warnings introduced
   - Formatting is consistent with codebase style

3. **Functionality:**
   - Server starts successfully
   - Key API endpoints work
   - No obvious regression

4. **Documentation:**
   - Implementation report is thorough
   - All commits are well-described
   - Issues are documented
   - Files modified are tracked

5. **Readiness for Stage 3:**
   - Clear what needs verification
   - Test scenarios identified
   - Success criteria defined
</verification>

<success_criteria>
The implementation is successful when:

1. ✅ bug-fix-implementation-report.md exists with complete details
2. ✅ All 47 bugs from bug-fix-plan.md are addressed
3. ✅ Server starts without critical errors
4. ✅ All batch verification tests pass
5. ✅ Git commits are logical and well-described
6. ✅ No new bugs introduced (to our knowledge)
7. ✅ Implementation report provides full context for Stage 3 verification
8. ✅ The report answers: "What did we fix and how do we know it worked?"
</success_criteria>

<constraints>
- DO make code changes following the plan exactly
- DO test after every fix
- DO commit after every successful batch
- DO rollback immediately if something breaks
- DO document deviations from plan
- DON'T skip verification steps
- DON'T accumulate untested changes
- DON'T refactor code while fixing bugs (out of scope)
- DON'T make changes beyond what's in the bug fix plan
- DON'T rush - thoroughness over speed
</constraints>

<parallel_tool_execution>
For maximum efficiency:
- Read multiple source files in parallel before editing
- Run multiple py_compile checks in parallel after changes
- Use grep to verify changes in parallel across files
</parallel_tool_execution>

<reflection_guidance>
After each fix:
1. Re-read the modified code to ensure correctness
2. Check if the fix actually addresses the root cause (not just symptoms)
3. Consider if the fix could break something else
4. Verify the change matches what the plan specified
5. Run the verification test and confirm it passes
6. Document any unexpected findings

After each batch:
1. Review all changes made in the batch
2. Assess if batch goals were achieved
3. Consider if next batch prerequisites are now met
4. Check if anything unexpected happened
5. Decide if it's safe to proceed or if investigation needed
</reflection_guidance>
