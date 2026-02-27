<objective>
Fix the Pacifica API authentication so that authenticated endpoints like /positions, /account/subaccounts, and /balance work correctly. The /info endpoint works (public), but authenticated endpoints are failing, preventing real position data from reaching the interface.
</objective>

<context>
The Positions Section implementation is complete and correct, but it's returning empty data because the Pacifica API authentication is broken. The issue is:

- ✅ /info endpoint works (public endpoint)
- ❌ /positions endpoint fails (requires authentication)
- ❌ /account/subaccounts endpoint fails (requires authentication)
- ❌ /balance endpoint fails (requires authentication)

This prevents real position data from flowing to the interface. The Positions Section code correctly handles JOIN queries, nested funding objects, and field mappings - it just needs working API data.
</context>

<requirements>
1. **Diagnose Authentication Issue**: Identify why authenticated endpoints fail while public ones work
2. **Fix Request Signing**: Ensure proper signature creation and header formatting
3. **Verify Credentials**: Confirm private key, public key, and account configuration
4. **Test Endpoints**: Verify /positions, /account/subaccounts, /balance work after fix
5. **Validate Data Flow**: Ensure positions flow from API → Database → Interface
6. **Handle Subaccounts**: Check if positions require specific subaccount queries
</requirements>

<implementation>
- Debug the _make_request method and signature creation
- Check authentication headers and payload formatting
- Verify Pacifica API expects the correct request format
- Test with different account/subaccount combinations
- Ensure proper error handling and logging
</implementation>

<output>
Fix authentication in pacifica_client.py and verify endpoints work.

Test results should show:
- /positions returns actual position data (if any exist)
- /account/subaccounts works
- /balance works
- Positions flow to interface correctly
</output>

<verification>
After implementation:
1. Test /positions endpoint returns success with position data
2. Test /account/subaccounts works
3. Test /balance works
4. Verify positions appear in interface (if any exist on account)
5. Check API logs for authentication errors
6. Confirm data flows: Pacifica API → Database JOIN → Interface
</verification>

<success_criteria>
- Pacifica authenticated endpoints (/positions, /account/subaccounts, /balance) return success
- Position data flows from API to interface correctly
- Interface displays real positions (or empty table if no positions exist)
- No authentication errors in logs
- Positions Section works with live data
</success_criteria>