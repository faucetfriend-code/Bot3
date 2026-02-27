<objective>
Fix the critical JavaScript errors in the trading bot interface without modifying the API server. The interface is calling undefined functions `checkBotStatus` and `updateRiskMetrics` that are causing repeated ReferenceErrors and preventing proper data updates.

The goal is to resolve all JavaScript errors in the interface while maintaining full functionality and ensuring the interface works correctly with the existing API server.
</objective>

<context>
This is a critical interface issue where the trading bot interface has JavaScript errors that are preventing proper functionality. From the error logs in `atemp.txt`, the interface is calling two undefined functions:

1. **`checkBotStatus` is not defined** - Called at line 4275, causing unhandled promise rejection
2. **`updateRiskMetrics` is not defined** - Called repeatedly in the `updateData` function, causing critical errors

These functions were likely removed or never implemented, but the interface code still references them. The user explicitly wants to NOT modify the API server, so all fixes must be contained within the interface files.

@trading_bot_interface.html - Contains the JavaScript code with undefined function calls
@atemp.txt - Error logs showing the specific ReferenceErrors and their locations
</context>

<requirements>
Fix all JavaScript errors in the interface without touching the API server:

1. **Implement or Remove `checkBotStatus`** - Either implement the missing function or safely remove its call
2. **Implement or Remove `updateRiskMetrics`** - Either implement the missing function or safely remove its call
3. **Fix Error Propagation** - Prevent JavaScript errors from breaking the interface functionality
4. **Maintain Data Updates** - Ensure all data loading and display continues to work
5. **Add Error Handling** - Implement proper error handling for any remaining function calls

The fixes must be contained entirely within the interface HTML/JavaScript and not require any server-side changes.
</requirements>

<diagnostic_approach>
<step_by_step_investigation>
Follow this systematic diagnostic process:

1. **Error Analysis**:
   - Locate all calls to `checkBotStatus` and `updateRiskMetrics` in the interface
   - Understand what these functions were supposed to do
   - Identify the context where they are called (DOM ready, data updates, etc.)

2. **Function Purpose Analysis**:
   - Determine if these functions are critical for interface operation
   - Check if they can be safely removed or need to be implemented
   - Analyze the impact of missing functionality

3. **Code Context Review**:
   - Examine the `updateData` function where `updateRiskMetrics` is called
   - Check the DOM ready handler where `checkBotStatus` is called
   - Verify all other function calls are valid

4. **Implementation Strategy**:
   - Decide whether to implement stub functions or remove the calls
   - Ensure error handling prevents interface breakage
   - Maintain all existing functionality
</step_by_step_investigation>

<common_interface_issues>
Check for these typical interface JavaScript issues:

- **Missing Function Implementations**: Functions declared but not defined
- **Removed Function Calls**: Code still calling functions that were deleted
- **Async Error Handling**: Unhandled promise rejections breaking interface
- **DOM Ready Issues**: Functions called before DOM is ready
- **Data Update Failures**: Errors in update functions preventing data refresh
</common_interface_issues>
</diagnostic_approach>

<implementation>
<diagnostic_tools>
Use these tools to systematically investigate the issues:

1. **Function Search**:
   - Search for all occurrences of `checkBotStatus` and `updateRiskMetrics`
   - Check if these functions are defined anywhere in the codebase
   - Verify function signatures and expected parameters

2. **Error Context Analysis**:
   - Examine the code around line 4275 where `checkBotStatus` is called
   - Check the `updateData` function around line 5845 where `updateRiskMetrics` is called
   - Understand the execution context and timing

3. **Function Purpose Determination**:
   - Analyze what `checkBotStatus` should do (likely check bot running status)
   - Determine what `updateRiskMetrics` should do (likely update risk display)
   - Check if these functions are essential or can be safely stubbed

4. **Interface Testing**:
   - Test interface loading without JavaScript errors
   - Verify data updates work after fixes
   - Ensure all UI components function correctly
</diagnostic_tools>

<repair_strategy>
Implement fixes that resolve the errors while maintaining functionality:

1. **For `checkBotStatus`**:
   - Either implement a stub function that safely checks bot status
   - Or remove the call if it's not critical for interface operation
   - Add proper error handling to prevent promise rejections

2. **For `updateRiskMetrics`**:
   - Either implement a stub function that updates risk displays
   - Or remove the call and handle the missing functionality gracefully
   - Ensure data updates continue to work without this function

3. **Error Prevention**:
   - Add try-catch blocks around function calls
   - Implement fallback behavior for missing functions
   - Ensure interface remains functional even with errors

4. **Code Safety**:
   - Add function existence checks before calling
   - Implement graceful degradation for missing features
   - Maintain all existing working functionality

**Critical Constraint**: Do NOT modify any server-side files. All changes must be contained within the interface HTML/JavaScript.
</repair_strategy>
</implementation>

<output>
Create fixes for the interface JavaScript errors:

1. **Error Analysis Report**:
   - `./diagnoses/interface-js-errors-analysis.md` - Analysis of the undefined function errors
   - Include locations, contexts, and proposed solutions

2. **Interface Fix Implementation**:
   - Modify `trading_bot_interface.html` to fix the JavaScript errors
   - Either implement missing functions or safely remove their calls
   - Add proper error handling to prevent future issues

3. **Test Results**:
   - `./diagnoses/interface-js-errors-fix-verification.md` - Verification that errors are resolved
   - Include before/after error logs and functionality tests

All changes must be contained within the interface file only - no server modifications allowed.
</output>

<constraints>
<critical_requirements>
- **No API Server Changes**: Absolutely do not modify any server-side files
- **Fix JavaScript Errors**: Resolve all ReferenceError issues in the interface
- **Maintain Functionality**: Ensure all existing interface features continue to work
- **Error Prevention**: Prevent JavaScript errors from breaking interface operation
- **Graceful Degradation**: Interface should work even if some functions are missing
</critical_requirements>

<technical_constraints>
- **Interface-Only Changes**: All modifications must be within `trading_bot_interface.html`
- **JavaScript Compatibility**: Maintain compatibility with existing browser requirements
- **Function Signatures**: If implementing functions, match expected call signatures
- **Error Handling**: Add proper error handling without breaking existing code
- **Performance**: Ensure fixes don't negatively impact interface performance
</technical_constraints>

<why_these_constraints_matter>
These constraints ensure the fixes are safe and contained. Server changes are explicitly forbidden by the user. JavaScript errors break the entire interface experience. Functionality must be preserved to maintain usability. Error prevention ensures stability. Interface-only changes prevent unintended side effects on the server.
</why_these_constraints_matter>
</constraints>

<verification>
Before declaring the JavaScript errors resolved, verify the fixes comprehensively:

1. **Error Resolution Verification**:
   - Interface loads without JavaScript console errors
   - No more "checkBotStatus is not defined" errors
   - No more "updateRiskMetrics is not defined" errors
   - No unhandled promise rejections

2. **Functionality Testing**:
   - All interface features work correctly
   - Data updates function properly
   - Bot status checking works (if implemented)
   - Risk metrics display correctly (if implemented)

3. **Error Handling Verification**:
   - Interface handles missing functions gracefully
   - No JavaScript errors break interface operation
   - Fallback behavior works when functions are unavailable

4. **Integration Testing**:
   - Complete interface workflow functions end-to-end
   - All UI components load and operate correctly
   - Data refresh and updates work without errors

5. **Regression Testing**:
   - Verify no existing functionality is broken by the fixes
   - Ensure interface performance is maintained
   - Test edge cases and error scenarios

<success_criteria>
The JavaScript errors are fully resolved when:

- ✅ **No ReferenceErrors**: Interface loads without "is not defined" errors
- ✅ **No Unhandled Rejections**: Promise rejections are properly handled
- ✅ **Functionality Preserved**: All existing interface features work correctly
- ✅ **Data Updates Working**: Interface successfully updates data without errors
- ✅ **Error Handling Added**: Missing functions don't break interface operation
- ✅ **Server Unchanged**: No modifications made to any server-side files

The interface operates smoothly without JavaScript errors while maintaining all existing functionality.
</success_criteria>
</verification>

<success_criteria>
The JavaScript errors are fully resolved when the interface loads and operates without any ReferenceError messages, unhandled promise rejections, or missing function calls, while maintaining all existing functionality and making no changes to the API server.
</success_criteria></content>
<parameter name="filePath">prompts/069-fix-interface-js-errors.md