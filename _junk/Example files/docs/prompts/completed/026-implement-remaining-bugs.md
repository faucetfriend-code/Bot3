<objective>
Continue bug fix implementation from where Stage 2 left off. Fix the remaining 33 bugs in Batches C, D, and E following the comprehensive bug fix plan.

This is a continuation of Stage 2 implementation: Batches A & B ✅ Complete → **Batches C, D, E** → Verification
</objective>

<context>
**Progress So Far:**
- ✅ Batch A: Critical Infrastructure (6 bugs) - COMPLETE
- ✅ Batch B: Exception Handling (8 bugs) - COMPLETE
- ⏳ Batch C: Security Hardening (5 bugs) - TO DO
- ⏳ Batch D: Database & API Logic (5 bugs) - TO DO
- ⏳ Batch E: Code Quality (23 bugs) - TO DO

**Total Progress:** 14 of 47 bugs fixed (30%)
**Remaining:** 33 bugs to fix

Read the comprehensive fix plan:
@bug-fix-plan.md

Read the implementation report from Batches A & B:
@bug-fix-implementation-report.md

Read bug catalogs:
@bugs.md
@missedbugs.md

Read architectural context:
@CLAUDE.md
</context>

<implementation_requirements>
Follow the same rigorous process used in Batches A & B:

### Core Principles:
1. **Follow the Plan** - Execute batches in order (C → D → E)
2. **One Batch at a Time** - Complete, test, commit before moving to next
3. **Continuous Verification** - Test after every fix
4. **Safe Modifications** - Minimal changes, preserve style
5. **Document Everything** - Track all changes and issues

### Workflow for Each Batch:

**Before Starting:**
1. Read batch description from bug-fix-plan.md
2. Review dependencies and prerequisites
3. Prepare verification tests

**During Execution:**
1. For each bug: read file → understand code → apply fix → verify
2. Run syntax check: `python -m py_compile [file]`
3. Document any deviations or surprises

**After Batch:**
1. Test server startup: `python api_server.py`
2. Run smoke tests
3. Commit: `git add [files] && git commit -m "fix: [description]"`
4. Update bug-fix-implementation-report.md

### Error Handling:
- If syntax error: fix immediately before proceeding
- If server won't start: rollback and investigate
- If test fails: understand root cause, revise approach
</implementation_requirements>

<batches_to_execute>

## Batch C: Security Hardening (5 bugs)
**Priority:** HIGH - Required for production deployment
**Estimated time:** 4-6 hours
**Risk:** Medium (touches authentication and API layer)

### Bugs in Batch C:
1. **BUG-016**: Missing input validation on account profile endpoints
2. **BUG-017**: No rate limiting on API endpoints
3. **BUG-018**: SQL injection risk (audit needed)
4. **BUG-019**: Missing HTTPS enforcement
5. **BUG-020**: Unsafe CORS configuration

**Execution Order:**
1. Start with CORS fix (easiest, api_server.py configuration)
2. Add input validation (Pydantic validators)
3. Implement rate limiting (slowapi middleware)
4. Add HTTPS support (uvicorn SSL configuration)
5. SQL injection audit last (requires thorough code review)

**After Batch C:**
- System is production-ready from security perspective
- Can deploy with real money (after verification)

---

## Batch D: Database & API Logic (5 bugs)
**Priority:** MEDIUM - Improves reliability
**Estimated time:** 8-12 hours
**Risk:** Medium (affects data layer and API contracts)

### Bugs in Batch D:
1. **BUG-011**: Missing table existence check before query
2. **BUG-012**: Race condition in balance sync
3. **BUG-013**: Unsafe float conversion with None
4. **BUG-014**: Missing connection close in early return paths
5. **BUG-015**: Inconsistent error response format

**Execution Order:**
1. Fix connection close issues (use context managers)
2. Fix unsafe float conversions (safe_float helper)
3. Add table schema validation
4. Fix race condition with lock
5. Standardize error response format

**After Batch D:**
- Database operations more robust
- API responses consistent
- Fewer race conditions and resource leaks

---

## Batch E: Code Quality (23 bugs)
**Priority:** LOW - Nice to have, improves maintainability
**Estimated time:** 12-16 hours
**Risk:** Low (mostly documentation and cleanup)

### Categories in Batch E:
1. **Type Safety** (5 bugs): Add missing type annotations
2. **Documentation** (4 bugs): Add docstrings and comments
3. **Code Cleanup** (6 bugs): Remove dead code, magic numbers
4. **Development Infrastructure** (8 bugs): Tests, CI/CD, tooling

**Execution Strategy:**
- Can be done incrementally
- Low risk, high value for long-term maintenance
- Consider doing in sub-batches over multiple sessions

**After Batch E:**
- Professional codebase quality
- Easy for new developers to understand
- Automated testing and CI/CD in place

</batches_to_execute>

<output_format>
Update the existing implementation report: `./bug-fix-implementation-report.md`

Add sections for each completed batch:

### Batch C: Security Hardening
**Status:** ✅ COMPLETE
**Time:** [actual time]
**Bugs Fixed:** #16, #17, #18, #19, #20

**What Was Done:**
- [Detailed description of each fix]

**Verification Results:**
- ✅ [Test 1]
- ✅ [Test 2]

**Files Modified:**
- [file list]

---

[Repeat for Batches D and E]

---

## Final Implementation Summary

**Total Bugs Fixed:** [X of 47]
**Batches Completed:** A, B, C, D, E
**Total Time:** [X hours]
**Files Modified:** [total count]
**Commits Made:** [commit list]

**System Status:**
✅ All critical bugs fixed
✅ All high-priority bugs fixed
✅ All medium bugs fixed
✅ Code quality improved

**Production Readiness:** READY (after Stage 3 verification)
</output_format>

<verification>
After completing each batch, verify:

**Batch C (Security):**
- ✅ CORS only allows specific origins
- ✅ Rate limiting responds with 429 when exceeded
- ✅ Input validation rejects invalid data
- ✅ HTTPS configuration present (even if self-signed)
- ✅ No SQL injection vulnerabilities found

**Batch D (Database/API):**
- ✅ Connection leaks eliminated (no leaked connections)
- ✅ Float conversions don't crash on None
- ✅ Race condition fixed (balance sync is thread-safe)
- ✅ Error responses have consistent format

**Batch E (Code Quality):**
- ✅ Type annotations present on all functions
- ✅ Docstrings on all public functions
- ✅ No dead code remaining
- ✅ Magic numbers converted to constants
- ✅ Basic tests exist
</verification>

<success_criteria>
Implementation is fully successful when:

1. ✅ All 47 bugs from bug-fix-plan.md are addressed
2. ✅ Batches C, D, E completed successfully
3. ✅ Server starts without errors
4. ✅ All verification tests pass
5. ✅ Security hardening complete
6. ✅ Database operations robust
7. ✅ Code quality professional
8. ✅ Implementation report updated
9. ✅ Git history clean with logical commits
10. ✅ System ready for Stage 3 verification
</success_criteria>

<constraints>
- DO follow the batch order: C → D → E
- DO test thoroughly after each batch
- DO commit after each successful batch
- DO update bug-fix-implementation-report.md
- DON'T skip verification steps
- DON'T rush through code quality improvements
- DON'T introduce new bugs while fixing old ones
- DON'T refactor beyond what's required
</constraints>

<priority_guidance>
**If time is limited:**

**Minimum (Production Ready):** Complete Batch C only
- Gets you security hardening
- Safe for production deployment
- ~4-6 hours of work

**Recommended (Stable & Secure):** Complete Batches C + D
- Security + reliability
- Professional quality
- ~12-18 hours of work

**Complete (Professional Quality):** All batches C + D + E
- Full bug fix
- Maintainable codebase
- ~24-34 hours of work

Choose based on your timeline and deployment needs.
</priority_guidance>

<parallel_tool_execution>
For maximum efficiency:
- Read multiple source files in parallel
- Run multiple syntax checks in parallel
- Verify multiple endpoints concurrently
</parallel_tool_execution>

<reflection_guidance>
After each batch:
1. Did we accomplish the batch goals?
2. Are there unexpected side effects?
3. Is the next batch safe to proceed?
4. Should we pause for user review?
5. Are we maintaining code quality?
</reflection_guidance>
