<objective>
Fix the account switcher TypeError that prevents users from switching between trading accounts in the interface.

The error `TypeError: Cannot set properties of null (setting 'value')` occurs when the system tries to set the selected account, blocking a critical feature for multi-account trading.
</objective>

<context>
The browser console shows an error during account profile loading:

```
(index):6476 Switch account error: TypeError: Cannot set properties of null (setting 'value')
    at Object.switchAccount ((index):6472:71)
    at async Object.loadAccountProfiles ((index):6329:25)
```

The flow appears to be:
1. ✅ Account profiles load successfully from server (Array with 1 profile)
2. ✅ Account switcher dropdown is populated with options
3. ❌ **FAILS** when trying to set the current account value

The error happens at line 6472, specifically when trying to set a `.value` property on a null element. This suggests the code is trying to access a DOM element that doesn't exist or hasn't been initialized yet.

**Successful steps shown in logs:**
- Account switcher element found: `<select id="accountSwitcher">`
- Profiles loaded: Array(1)
- Option added: "⭐ Default Account (Testnet)"

**Failure point:**
The `switchAccount` function tries to set a value on an element that is null, likely trying to set a display element or hidden input that doesn't exist.

Read the interface file to understand the account switching mechanism:
@trading_bot_interface.html
</context>

<requirements>
1. **Locate the error**: Find line 6472 in trading_bot_interface.html where the null property is being set
2. **Identify the missing element**: Determine which DOM element is null
3. **Fix the reference**: Either:
   - Add the missing HTML element if it should exist
   - Add null check before setting the property
   - Fix the element ID mismatch if names don't match
4. **Verify the full flow**: Ensure account switching works end-to-end
5. **Test with multiple accounts**: Verify the switcher works with 1+ accounts
</requirements>

<implementation>
**Investigation steps:**
1. Find the `switchAccount` function around line 6472
2. Identify what element `.value` is being set on
3. Check if that element exists in the HTML
4. Trace back from `loadAccountProfiles` to see the initialization flow

**Common patterns causing this error:**
```javascript
// Pattern 1: Element doesn't exist
const someElement = document.getElementById('nonexistent');
someElement.value = 'something'; // TypeError: Cannot set properties of null

// Pattern 2: Element ID mismatch
// HTML: <input id="currentAccount">
// JS: document.getElementById('selectedAccount').value = 'x'; // null

// Pattern 3: Element not yet in DOM
// JS runs before HTML is fully loaded
```

**Fix approaches:**

**Option A - Add null check (defensive)**:
```javascript
const element = document.getElementById('elementId');
if (element) {
    element.value = newValue;
} else {
    console.warn('Element not found:', 'elementId');
}
```

**Option B - Add missing HTML element**:
If the element should exist but doesn't, add it to the HTML structure.

**Option C - Fix ID mismatch**:
Ensure JavaScript and HTML use consistent element IDs.

**Why this matters**: Multi-account trading is a core feature. Users with multiple Pacifica subaccounts need to switch between them to view different portfolios and execute trades on specific accounts. A broken switcher makes the multi-account feature completely unusable.
</implementation>

<output>
Modify `./trading_bot_interface.html`:
- Fix the null reference at line 6472 in the switchAccount function
- Add missing HTML element if needed
- Add null checks for optional/dynamic elements
- Ensure account switcher works with the loaded profiles
</output>

<verification>
Before declaring complete, verify your fix:

1. **Find the exact error location**:
   ```bash
   # Line 6472 should show what .value is being set on
   ```

2. **Check account-related elements in HTML**:
   - Account switcher dropdown: `<select id="accountSwitcher">`
   - Any hidden inputs for current account
   - Display elements showing selected account

3. **Test the fix**:
   - Account switcher dropdown should populate
   - Selecting an account should not throw errors
   - Account selection should persist and update the UI

Success criteria:
- No `TypeError: Cannot set properties of null` error
- Account switcher dropdown populates with profiles
- Selecting an account updates the UI appropriately
- Console shows successful account switch: "✅ Switched to account: [name]" (or similar)
</verification>

<success_criteria>
✅ No TypeError when switching accounts
✅ Account profiles load and populate the dropdown
✅ Selecting an account works without errors
✅ The selected account is properly stored/displayed
✅ Multi-account functionality is fully operational
</success_criteria>
