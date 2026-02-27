<objective>
Diagnose and repair the safeguard system issues that are incorrectly reporting critical files as missing when they actually exist. This is preventing proper server startup and creating false alarms that block legitimate operations.

The goal is to fix the flawed file existence checks and safeguard logic so that the system correctly identifies when files are present and only blocks startup when there are genuine issues.
</objective>

<context>
This is a critical infrastructure issue in the trading bot project where the safeguard system (`api_server_safeguards.py`) is malfunctioning. The safeguards are designed to prevent server startup when critical components are missing, but they're incorrectly reporting that essential files exist when they actually do.

From the error log in `atemp.txt`, the safeguards are failing on:
- File existence checks for core files (api_server.py, database.py, config.py, main.py, trading_bot_interface.html)
- Database schema validation (missing 'profiles' table)
- Network connectivity tests (timing out)

The server is currently bypassing these safeguards to start, but this undermines the safety mechanisms that were put in place to prevent untested deployments.

@api_server_safeguards.py - The safeguard implementation with faulty logic
@atemp.txt - Error log showing the specific safeguard failures
@api_server.py - Server startup code that integrates safeguards
</context>

<requirements>
Thoroughly diagnose and repair the safeguard system issues:

1. **Analyze File Existence Logic**: Determine why file existence checks are failing for files that clearly exist
2. **Fix Path Resolution**: Correct any path resolution issues in the safeguard checks
3. **Repair Database Schema Validation**: Fix the logic for checking required database tables
4. **Improve Network Connectivity Tests**: Make network checks more reliable and less prone to timeouts
5. **Enhance Error Reporting**: Provide clearer, more actionable error messages
6. **Add Safeguard Testing**: Create tests to verify safeguard functionality works correctly

The diagnosis must identify the root cause of each safeguard failure and implement proper fixes.
</requirements>

<diagnostic_approach>
<step_by_step_investigation>
Follow this systematic diagnostic process:

1. **File Existence Check Analysis**:
   - Examine how file existence checks are implemented in `api_server_safeguards.py`
   - Check path resolution logic and working directory assumptions
   - Test file existence checks manually for each reported missing file
   - Verify file permissions and accessibility

2. **Path Resolution Investigation**:
   - Determine the working directory when safeguards run
   - Check if relative paths are being resolved correctly
   - Verify that the safeguard code can access the project root directory
   - Test path resolution with absolute vs relative paths

3. **Database Schema Validation**:
   - Examine the database connection and schema checking logic
   - Verify that database tables are being checked correctly
   - Test database connectivity and table existence manually
   - Check if the 'profiles' table creation logic is working

4. **Network Connectivity Testing**:
   - Analyze the network connectivity check implementation
   - Test the external service (httpbin.org) accessibility
   - Consider more reliable network connectivity tests
   - Implement timeout handling and fallback logic

5. **Integration Testing**:
   - Test safeguards in isolation from server startup
   - Verify safeguard results are being interpreted correctly
   - Check that bypass logic is working as intended
   - Test complete safeguard suite with known good/bad conditions
</step_by_step_investigation>

<common_safeguard_issues>
Check for these typical safeguard implementation problems:

- **Working Directory Issues**: Safeguards running from wrong directory, breaking relative paths
- **Path Resolution Errors**: Incorrect path joining or absolute path assumptions
- **File Permission Problems**: Files exist but aren't readable by the safeguard process
- **Database Connection Issues**: Database checks failing due to connection problems, not missing tables
- **Network Timeout Issues**: External service checks timing out due to network issues
- **Logic Errors**: Incorrect boolean logic in pass/fail conditions
- **Import Path Issues**: Safeguard code can't import the modules it's trying to check
</common_safeguard_issues>
</diagnostic_approach>

<implementation>
<diagnostic_tools>
Use these tools to systematically investigate the issues:

1. **File System Testing**:
   - Test file existence checks manually: `python -c "import os; print(os.path.exists('api_server.py'))"`
   - Check working directory: `python -c "import os; print(os.getcwd())"`
   - Verify file permissions: `python -c "import os; print(oct(os.stat('api_server.py').st_mode))"`

2. **Database Testing**:
   - Test database connectivity: `python -c "from database import DatabaseManager; db = DatabaseManager(); print('Connected')"`
   - Check table existence: `python -c "from database import DatabaseManager; db = DatabaseManager(); print(db.table_exists('profiles'))"`

3. **Network Testing**:
   - Test external connectivity: `curl -I --max-time 5 http://httpbin.org/get`
   - Check DNS resolution: `nslookup httpbin.org`

4. **Safeguard Isolation Testing**:
   - Run safeguards independently: `python -c "from api_server_safeguards import run_safeguards; results = run_safeguards(); print(results)"`
   - Test individual safeguard functions in isolation

5. **Debug Logging**:
   - Enable detailed logging in safeguards
   - Add debug prints to understand execution flow
   - Log intermediate values and decision points
</diagnostic_tools>

<repair_strategy>
Once the root causes are identified, implement fixes following these principles:

1. **Fix Path Resolution**: Use absolute paths or proper relative path resolution
2. **Improve File Checks**: Add more robust file existence and accessibility checks
3. **Fix Database Logic**: Correct database schema validation logic
4. **Enhance Network Tests**: Make network connectivity checks more reliable
5. **Add Error Handling**: Implement proper exception handling and fallback logic
6. **Improve Logging**: Add detailed logging for debugging safeguard issues

Specific repair approaches:
- Use `os.path.abspath()` for reliable path resolution
- Add file readability checks in addition to existence checks
- Implement database connection retry logic
- Use multiple network test endpoints with fallbacks
- Add safeguard self-testing capabilities
- Implement graceful degradation for non-critical safeguards
</repair_strategy>
</implementation>

<output>
Create diagnostic reports and implement fixes:

1. **Diagnostic Report**:
   - `./diagnoses/safeguard-system-diagnosis.md` - Detailed analysis of safeguard failures
   - Include root cause analysis and specific issues found

2. **Fix Implementation**:
   - Modify `api_server_safeguards.py` to fix the identified issues
   - Update safeguard logic for proper file existence checks
   - Improve database and network connectivity validation

3. **Test Results**:
   - `./diagnoses/safeguard-fix-verification.md` - Verification that safeguards work correctly
   - Include before/after test results and safeguard status

4. **Prevention Measures**:
   - Add safeguard unit tests to prevent future regressions
   - Update documentation with troubleshooting guide

All changes should be minimal, targeted, and thoroughly tested.
</output>

<constraints>
<critical_requirements>
- **Fix False Positives**: Safeguards must correctly identify when files exist
- **Maintain Security**: Don't weaken safeguards that are actually working
- **Preserve Functionality**: Server startup bypass should only be used when necessary
- **Improve Reliability**: Make safeguards more robust and less prone to false failures
- **Clear Diagnostics**: Provide actionable error messages when safeguards fail legitimately
</critical_requirements>

<diagnostic_constraints>
- **Systematic Approach**: Follow the diagnostic steps in order, don't skip steps
- **Evidence-Based**: Base conclusions on observable evidence, not assumptions
- **Thorough Documentation**: Document every finding and step taken
- **Reproducible Results**: Ensure the diagnosis can be reproduced by others
</diagnostic_constraints>

<why_these_constraints_matter>
These constraints ensure the diagnosis is reliable and the fixes are safe. False positive safeguards create unnecessary friction for developers. Weakening working safeguards reduces system security. Skipping diagnostic steps could miss the real root cause. Without thorough documentation, future developers can't understand what was fixed.
</why_these_constraints_matter>
</constraints>

<verification>
Before declaring the safeguard issues resolved, verify the fixes comprehensively:

1. **File Existence Verification**:
   - Test safeguards with all critical files present: should pass
   - Test safeguards with missing files: should fail appropriately
   - Verify path resolution works from different working directories

2. **Database Schema Testing**:
   - Test with properly initialized database: should pass
   - Test with missing tables: should fail with clear error message
   - Verify database connectivity checks work correctly

3. **Network Connectivity Testing**:
   - Test with good network connectivity: should pass
   - Test with poor connectivity: should handle timeouts gracefully
   - Verify fallback logic works when primary network test fails

4. **Integration Testing**:
   - Test complete server startup with safeguards enabled
   - Verify bypass logic only activates when necessary
   - Test safeguard reporting and error message clarity

5. **Regression Testing**:
   - Run safeguard tests to ensure no existing functionality is broken
   - Test edge cases and error scenarios
   - Verify safeguards work across different environments

<success_criteria>
The safeguard system diagnosis and repair is successful when:

- ✅ **File Checks Work**: Safeguards correctly identify existing files
- ✅ **No False Positives**: Safeguards don't block startup when files exist
- ✅ **Database Validation Works**: Schema checks function correctly
- ✅ **Network Tests Reliable**: Connectivity checks are robust and don't timeout inappropriately
- ✅ **Server Starts Properly**: Server can start without bypass when safeguards pass
- ✅ **Clear Error Messages**: Failed safeguards provide actionable guidance
- ✅ **Comprehensive Testing**: All verification steps pass with proper safeguard behavior

The safeguard system now provides reliable protection without creating unnecessary barriers to legitimate server operations.
</success_criteria>
</verification>

<success_criteria>
The safeguard issues are fully resolved when the system correctly identifies existing files, provides reliable database and network validation, and only blocks server startup when there are genuine issues requiring attention.
</success_criteria></content>
<parameter name="filePath">prompts/067-fix-safeguard-system-issues.md