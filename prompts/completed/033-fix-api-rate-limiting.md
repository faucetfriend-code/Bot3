<objective>
Fix Pacifica API Rate Limiting Issue in the trading bot by implementing retry logic with exponential backoff, adding rate limit handling in sync operations, and considering caching strategies to reduce API calls.
</objective>

<context>
This is for the trading bot project (trading_bot_v2) that uses Pacifica for Solana perpetuals trading. The current issue is 429 errors from Pacifica API causing sync failures.

Reference the following documentation for Pacifica API details:
- @Example files/docs/context files/pacifica/rate_limits.md
- @Example files/docs/context files/pacifica/api_reference_rest.md
- @Example files/docs/context files/pacifica/authentication.md

Reference code examples from:
- @Example files/utilities/python-sdk

Who will use this: Trading bot users experiencing sync failures due to rate limits.
End goal: Reliable position syncing without 429 errors.
</context>

<requirements>
1. Implement retry logic with exponential backoff for API calls that return 429 errors
2. Add rate limit handling specifically in sync operations (api_server.py sync endpoint)
3. Consider caching strategies to reduce the number of API calls needed
4. Ensure the solution works with Pacifica's rate limit policies as documented
5. Maintain backward compatibility with existing sync functionality
</requirements>

<implementation>
- Use exponential backoff: start with 1 second, double each retry, max 5 retries
- Check rate_limits.md for specific rate limit details and headers
- Implement caching for position data to avoid unnecessary API calls
- Add proper error handling and logging for rate limit scenarios
- Follow patterns from python-sdk examples for API interaction
</implementation>

<output>
Modify files with relative paths:
- ./trading_bot_v2/api_server.py - Add retry logic and rate limit handling to sync functions
- ./trading_bot_v2/database.py - Add caching layer if needed for position data
</output>

<verification>
Before declaring complete, verify:
- Sync operations handle 429 errors gracefully with retries
- No more "Failed to fetch" errors due to rate limits
- API calls are optimized to stay within rate limits
- Test with real Pacifica API to confirm rate limit handling works
</verification>

<success_criteria>
- Sync endpoint successfully handles rate limited responses
- Exponential backoff implemented and working
- Caching reduces API call frequency
- No 429 errors in normal operation
- Logging provides clear rate limit status information
</success_criteria></content>
<parameter name="filePath">prompts/033-fix-api-rate-limiting.md

---
Completed at: 2026-01-13T23:43:33.399Z
