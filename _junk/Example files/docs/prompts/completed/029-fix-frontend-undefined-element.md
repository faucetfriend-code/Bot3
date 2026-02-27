<objective>
Fix the critical frontend JavaScript error causing `ReferenceError: availableMarginEl is not defined` that crashes the data update loop in the trading bot interface.

This error breaks the main data refresh cycle, preventing the UI from displaying real-time trading data and bot status updates.
</objective>

<context>
The browser console shows a critical error in the updateStatusDisplay function:

```
(index):4989 Critical error updating data: ReferenceError: availableMarginEl is not defined
    at updateStatusDisplay ((index):5021:17)
    at updateData ((index):4865:25)
```

This error occurs when the main `updateData()` function calls `updateStatusDisplay()`, which tries to access a DOM element (`availableMarginEl`) that either:
1. Doesn't exist in the HTML
2. Wasn't properly initialized/cached
3. Has a different ID than expected

The error is particularly critical because it occurs in the main data polling loop, causing the entire interface to stop updating.

Read the interface file to understand the DOM structure and JavaScript initialization:
@trading_bot_interface.html
</context>

<requirements>
1. **Locate the error source**: Find line 5021 in trading_bot_interface.html where `availableMarginEl` is referenced
2. **Identify root cause**: Determine if the element exists in HTML and why the reference is undefined
3. **Fix the reference**: Either:
   - Add the missing DOM element to HTML if it doesn't exist
   - Fix the element caching in the initialization code
   - Add defensive null checks before accessing the element
4. **Verify all related elements**: Check if other margin-related elements have the same issue
5. **Test the fix**: Ensure updateStatusDisplay can run without errors
</requirements>

<implementation>
**Investigation steps:**
1. Search for `availableMarginEl` in trading_bot_interface.html
2. Find where DOM element references are cached (likely near line 5021)
3. Check if there's an HTML element with ID `availableMargin` or similar
4. Look for the initialization pattern used for other elements

**Common patterns to check:**
```javascript
// Element caching (usually in initialization)
const availableMarginEl = document.getElementById('availableMargin');

// Usage in updateStatusDisplay
availableMarginEl.textContent = formatCurrency(availableBalance);
```

**Fix approaches (choose the correct one based on findings):**

**Option A - Missing element cache initialization:**
If the element exists in HTML but wasn't cached, add it to the initialization block.

**Option B - Missing HTML element:**
If the HTML element doesn't exist, add it to the appropriate section of the interface.

**Option C - Defensive programming:**
If the element is optional, add null checks:
```javascript
if (availableMarginEl) {
    availableMarginEl.textContent = formatCurrency(availableBalance);
}
```

**Why this matters**: The updateData function is called every 3-5 seconds. If it crashes, the entire interface becomes stale and useless. We need bulletproof error handling here because this is the heartbeat of the UI.
</implementation>

<output>
Modify `./trading_bot_interface.html`:
- Fix the undefined element reference at line 5021
- Add any missing HTML elements if needed
- Ensure all DOM element references are properly initialized
- Add defensive null checks for optional elements
</output>

<verification>
Before declaring complete, verify your fix:

1. **Search for the fix location**:
   ```bash
   grep -n "availableMarginEl" trading_bot_interface.html
   ```

2. **Check related elements**: Look for similar patterns with other margin/balance elements
3. **Verify HTML structure**: Confirm the element exists or was added
4. **Test the change**: The fix should prevent the ReferenceError without breaking existing functionality

Success criteria:
- No `ReferenceError: availableMarginEl is not defined` in console
- updateStatusDisplay executes without errors
- Related balance/margin elements display correctly
</verification>

<success_criteria>
✅ The undefined element reference is fixed
✅ updateStatusDisplay can execute without throwing errors
✅ The data polling loop runs continuously without crashes
✅ Balance and margin information displays correctly in the UI
</success_criteria>
