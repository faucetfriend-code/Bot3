<objective>
Fix the malformed API URLs and excessive 404 errors that occur when opening the trading interface. The bot is generating incorrect URL syntax with mixed query parameter separators and attempting to fetch positions for unsupported symbols, causing a cascade of API errors.

This matters because the trading interface becomes unusable due to the flood of error messages, and the malformed URLs indicate a fundamental issue with API request construction.
</objective>

<context>
This is for the FastAPI-based trading bot where the server loads successfully but the web interface triggers numerous API errors. The errors show malformed URLs with incorrect query parameter syntax (`&symbol=XRP?account=...` instead of `?symbol=XRP&account=...`) and 404 responses for position requests on unsupported symbols.

Key files to examine:
@pacifica_client.py - API client with URL construction and request logic
@api_server.py - server endpoints that trigger position fetching when interface loads

Reference the python-sdk directory at "C:\Users\z_shi\Desktop\N8NPROJECTS\trade bot\python-sdk" for correct API usage patterns.

The interface appears to be fetching positions for all available symbols, but many return 404 errors.
</context>

<requirements>
1. Fix the malformed URL construction in position API calls (replace `&symbol=?account=` with `?symbol=&account=`)
2. Implement proper error handling for 404 responses to prevent error floods
3. Filter out unsupported symbols before making API calls
4. Add rate limiting or batching for position requests to prevent overwhelming the API
5. Ensure the interface loads without triggering excessive API errors

The fixes should make the trading interface functional without the current error cascade.
</requirements>

<implementation>
Analyze the position fetching logic:
- The URL construction is using incorrect parameter separators
- The interface is attempting to fetch positions for all symbols simultaneously
- 404 errors are not being handled gracefully, causing error message floods
- Need to validate symbols before API calls and handle missing positions appropriately

Fix approach:
- Correct URL parameter construction in pacifica_client.py
- Add symbol validation/filtering before API calls
- Implement proper 404 error handling (treat as "no position" rather than error)
- Consider batching or limiting concurrent position requests
- Update interface loading logic to be more selective about position fetching
</implementation>

<constraints>
- Do not break existing API authentication or connection logic
- Maintain the ability to fetch positions when they exist
- Keep API rate limiting intact
- Ensure fixes work in both testnet and mainnet environments
- Do not remove position fetching entirely, only make it more efficient and error-free
</constraints>

<output>
Modify file with relative paths:
- ./pacifica_client.py - fix URL construction and error handling for positions
- ./api_server.py - update position fetching logic to be more selective

Document the specific URL fixes and error handling improvements made.
</output>

<verification>
Before declaring complete, verify your work by:

1. Successfully start the server and open the trading interface
2. Confirm no malformed URLs in API request logs
3. Verify 404 errors are handled gracefully without error floods
4. Ensure interface loads properly with position data where available
5. Check that only valid/supported symbols trigger position requests

Use server logs to confirm clean API calls and proper error handling.
</verification>

<success_criteria>
- Server starts and interface loads without error cascades
- No malformed URLs with mixed `&` and `?` parameter separators
- 404 responses for positions are handled gracefully (not logged as errors)
- Interface displays available position data correctly
- No excessive API call volume when loading the interface
</success_criteria>