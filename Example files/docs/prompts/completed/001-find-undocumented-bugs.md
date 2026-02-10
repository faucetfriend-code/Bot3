<objective>
Conduct a comprehensive audit of the trading bot codebase to identify ALL bugs, errors, code issues, and quality problems that exist in the project but are NOT documented in bugs.md.

This audit will create a complete picture of technical debt and undocumented issues to ensure nothing is overlooked during development and maintenance.
</objective>

<context>
This is a Pacifica.fi algorithmic trading bot built with FastAPI (Python) backend and vanilla JavaScript frontend. The project has an existing bugs.md file that catalogs 12 known issues. However, there are likely many more undocumented problems lurking in the codebase.

The audit must be thorough and systematic, examining:
- All Python backend files (api_server.py, main.py, database.py, execution.py, etc.)
- Frontend files (trading_bot_interface.html and any JavaScript)
- Configuration files (.env, config.py)
- Database schema and migrations
- Testing infrastructure
- Documentation and comments

First, read bugs.md to understand what's already documented:
@bugs.md

Then read the project overview to understand the architecture:
@CLAUDE.md
</context>

<analysis_requirements>
Perform a comprehensive audit across these dimensions:

1. **Syntax and Structure Issues**
   - Python syntax errors (indentation, unclosed brackets/quotes, invalid statements)
   - JavaScript syntax errors in HTML file
   - JSON parsing errors in config files
   - Undefined variables and functions
   - Import errors and circular dependencies
   - Missing return statements in functions that should return values

2. **Logic and Calculation Bugs**
   - Incorrect mathematical calculations (especially P&L, funding rates, position sizing)
   - Wrong conditional logic (if/else conditions that don't match intent)
   - Off-by-one errors in loops and array indexing
   - Incorrect data type conversions
   - Race conditions and timing issues
   - State management bugs (incorrect status transitions)

3. **Exception Handling Gaps**
   - Missing try/except blocks around risky operations
   - Bare except clauses that swallow all errors
   - Unhandled error cases and edge conditions
   - Missing validation before operations
   - Silent failures that should be logged
   - Resource leaks (unclosed database connections, file handles)

4. **Developer Comments Indicating Issues**
   - TODO comments describing incomplete work
   - FIXME comments marking known problems
   - XXX or HACK comments indicating technical debt
   - Commented-out code that suggests uncertainty
   - WARNING comments about dangerous operations

5. **Database and Data Issues**
   - SQL query errors (incorrect syntax, missing tables/columns)
   - Database connection management problems
   - Transaction handling issues
   - Data integrity violations
   - Missing foreign key constraints
   - Incorrect database path usage (hardcoded vs DATABASE_PATH)

6. **API and Integration Issues**
   - Incorrect API endpoint paths
   - Missing authentication handling
   - Response format mismatches between frontend and backend
   - WebSocket connection problems
   - Rate limiting issues
   - Incorrect HTTP methods or status codes

7. **Performance and Resource Issues**
   - Memory leaks
   - Inefficient queries or loops
   - Missing pagination
   - Redundant API calls
   - Blocking operations in async contexts
   - Missing caching where needed

8. **Security Vulnerabilities**
   - Hardcoded credentials or API keys
   - SQL injection vulnerabilities
   - Missing input validation
   - Exposed sensitive data in logs
   - Insecure authentication flows
   - Missing CORS configuration

9. **Type and Null Safety Issues**
   - Type annotation errors
   - None/null reference errors
   - Type mismatches in function calls
   - Missing null checks before operations
   - Incorrect optional type handling

10. **Code Quality and Maintainability Issues**
    - Dead code that's never called
    - Duplicate code that should be refactored
    - Functions that are too long or complex
    - Inconsistent naming conventions
    - Missing or outdated documentation
    - Test coverage gaps

For each category, thoroughly explore multiple sources and consider various perspectives. Use grep patterns, file reading, and code analysis to identify issues systematically.
</analysis_requirements>

<research>
Use a systematic approach to explore the codebase:

1. **File Discovery Phase**
   - Use Glob to find all Python files: `**/*.py`
   - Find all HTML/JS files: `**/*.html`, `**/*.js`
   - Find config files: `**/*.json`, `**/*.env*`, `**/config.py`
   - Find database files: `**/*.db`, `**/*.sql`

2. **Pattern Searching Phase**
   For each issue category, use Grep with appropriate patterns:
   - Syntax issues: Look for common error patterns
   - TODO/FIXME: Search for `TODO|FIXME|XXX|HACK|WARNING`
   - Exception handling: Search for `except:` (bare excepts), missing try blocks
   - Import errors: Search for import statements and verify they exist
   - Undefined variables: Look for variable usage before definition
   - Database issues: Search for `sqlite3.connect`, `cursor.execute`

3. **Code Reading Phase**
   For critical files, read them completely to understand:
   - api_server.py (main API endpoints)
   - main.py (bot orchestration)
   - database.py (data layer)
   - execution.py (trading logic)
   - risk.py (risk calculations - critical for financial accuracy)
   - pacifica_client.py (external API integration)
   - trading_bot_interface.html (frontend logic)

4. **Cross-Reference Phase**
   Compare bugs.md against your findings to identify what's NOT documented
</research>

<output_format>
Create a comprehensive report saved to: `./missedbugs.md`

Use this structure:

```markdown
# Undocumented Bugs and Issues Report

Generated: [timestamp]
Total Issues Found: [count]
Already Documented in bugs.md: [count]
**New/Undocumented Issues: [count]**

---

## Executive Summary

[2-3 paragraph overview of the audit findings, highlighting the most critical undocumented issues]

---

## Critical Issues (Not in bugs.md)

### [Issue Number]: [Issue Title]
**File:** [file path with line numbers]
**Category:** [Syntax/Logic/Exception/etc.]
**Severity:** Critical
**Status:** Undocumented

**Description:**
[Clear explanation of the issue]

**Evidence:**
```[language]
[Code snippet showing the problem]
```

**Impact:**
[What breaks or behaves incorrectly due to this issue]

**Why This Matters:**
[Business/technical context for why this is critical]

**Recommended Fix:**
[Suggested approach to resolve]

---

[Repeat for all critical issues]

## High Priority Issues (Not in bugs.md)

[Same format as critical issues]

## Medium Priority Issues (Not in bugs.md)

[Same format but can be slightly more concise]

## Low Priority Issues (Not in bugs.md)

[Brief entries for minor issues]

---

## Issue Categories Breakdown

| Category | Count | Critical | High | Medium | Low |
|----------|-------|----------|------|--------|-----|
| Syntax Errors | X | X | X | X | X |
| Logic Bugs | X | X | X | X | X |
| Exception Handling | X | X | X | X | X |
| Database Issues | X | X | X | X | X |
| API Issues | X | X | X | X | X |
| TODO/FIXME Comments | X | X | X | X | X |
| Security Issues | X | X | X | X | X |
| Performance Issues | X | X | X | X | X |
| Type Safety Issues | X | X | X | X | X |
| Code Quality | X | X | X | X | X |

---

## Files Audited

- [file path] - [issue count] issues found
- [file path] - [issue count] issues found
[List all files examined]

---

## Comparison with bugs.md

**Issues in bugs.md that are correctly documented:**
- [List issues that are properly captured]

**Issues found in both audit and bugs.md:**
- [List overlaps]

**Issues ONLY found in this audit (the undocumented bugs):**
- [List new discoveries - this is the key section]

---

## Recommendations

1. **Immediate Actions:**
   - [Fix critical undocumented issues that block functionality]

2. **Short-term Actions:**
   - [Address high-priority bugs that impact user experience]

3. **Long-term Actions:**
   - [Code quality improvements, refactoring, technical debt]

4. **Testing Improvements:**
   - [Gaps in test coverage that allowed these bugs to exist]

---

## Methodology Notes

**Audit Scope:**
- [List files and directories examined]
- [Search patterns used]
- [Analysis tools employed]

**Limitations:**
- [Note any areas not examined or limitations of the audit]

**Confidence Level:**
[High/Medium/Low confidence that this audit is comprehensive]
```
</output_format>

<verification>
Before declaring the audit complete, verify:

1. **Completeness Check:**
   - All major Python files have been examined (api_server.py, main.py, database.py, execution.py, risk.py, etc.)
   - Frontend file (trading_bot_interface.html) has been analyzed
   - Configuration files have been checked
   - All 10 issue categories have been investigated

2. **Comparison Check:**
   - bugs.md has been read and understood
   - Each finding has been cross-referenced against bugs.md
   - Clear distinction made between documented vs undocumented issues

3. **Evidence Check:**
   - Every reported issue has file path and line numbers
   - Code snippets are provided as evidence
   - Impact is clearly explained

4. **Prioritization Check:**
   - Issues are properly categorized by severity (Critical/High/Medium/Low)
   - Rationale for severity levels is clear
   - Most critical undocumented issues are highlighted in executive summary

5. **Quality Check:**
   - Report is well-organized and scannable
   - Technical accuracy is high
   - Recommendations are actionable
   - No false positives or misunderstandings

6. **Count Verification:**
   - Total issue count matches number of issues listed
   - Category breakdown adds up correctly
   - "New/Undocumented" count is accurate
</verification>

<success_criteria>
The audit is successful when:

1. ✅ missedbugs.md file exists with comprehensive findings
2. ✅ All 10 issue categories have been investigated systematically
3. ✅ At least 50 files have been examined (or all files if fewer exist)
4. ✅ Clear distinction between documented (in bugs.md) vs undocumented issues
5. ✅ Every undocumented issue includes: file path, line numbers, code evidence, impact statement
6. ✅ Executive summary highlights the most critical undocumented findings
7. ✅ Recommendations are prioritized and actionable
8. ✅ Report includes methodology notes and confidence assessment
9. ✅ The report answers: "What critical bugs exist that we didn't know about?"
</success_criteria>

<constraints>
- Do NOT make any code changes - this is analysis only
- Do NOT run the trading bot or execute risky operations
- Include line numbers for all issues to enable quick fixes
- Focus on UNDOCUMENTED issues - don't repeat what's already in bugs.md unless providing additional context
- Be thorough but accurate - false positives reduce trust in the audit
- For ambiguous cases, explain why something might be an issue rather than declaring it definitively wrong
</constraints>

<parallel_tool_execution>
For maximum efficiency, whenever you need to perform multiple independent operations, invoke all relevant tools simultaneously rather than sequentially. For example:
- Read multiple files in parallel
- Run multiple grep searches in parallel
- Use glob patterns in parallel for different file types
</parallel_tool_execution>

<reflection_guidance>
After receiving tool results, carefully reflect on their quality and determine optimal next steps before proceeding. If you find a potential issue:
1. Verify it's actually a bug (not intentional behavior)
2. Check if it's already documented in bugs.md
3. Assess its severity based on actual impact
4. Gather sufficient evidence before including it in the report
</reflection_guidance>
