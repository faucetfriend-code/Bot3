<objective>
Fix the `/api/server/status` endpoint which is returning 500 Internal Server Error, causing the frontend to repeatedly fail fetching server status and preventing the interface from loading critical bot information.

This endpoint is essential for the frontend to determine if the server is running and what state the bot is in.
</objective>

<context>
The browser console shows repeated 500 errors for the `/api/server/status` endpoint:

```
:8000/api/server/status:1  Failed to load resource: the server responded with a status of 500 (Internal Server Error)
(index):3048 API call failed (attempt 1/3), retrying in 1620ms...
```

The frontend retries this call multiple times but continues to fail. This is a NEW endpoint error that appeared after we fixed the `/api/status` endpoint earlier (which now works correctly).

**Key distinction**:
- `/api/status` - Working correctly, returns bot status
- `/api/server/status` - **BROKEN**, returning 500 errors

The frontend JavaScript is calling BOTH endpoints, and the `/api/server/status` failure is blocking proper initialization.

Check the API server for this endpoint:
@api_server.py
</context>

<requirements>
1. **Locate the endpoint**: Find the `/api/server/status` route in api_server.py
2. **Identify the error**: Check server logs (the background bash process) for the actual exception
3. **Fix the endpoint**: Repair the code causing the 500 error
4. **Verify the fix**: Test that the endpoint returns valid JSON
5. **Check for similar patterns**: Ensure we haven't introduced the same bug elsewhere
</requirements>

<implementation>
**Investigation steps:**
1. Search for the route definition: `@app.get("/api/server/status")` in api_server.py
2. Check the background server logs (bash ID 245775) for the actual Python traceback
3. Compare with the working `/api/status` endpoint to identify differences
4. Look for recently added code or variables that might be undefined

**Common 500 error causes:**
- Undefined variable (similar to the `unrealized_pnl` bug we fixed earlier)
- Missing import or module
- Database connection error
- Incorrect data type casting
- Missing exception handling

**Why /api/server/status exists**: This endpoint likely provides server-level information (uptime, health checks, system metrics) separate from bot-specific status. The frontend needs both to display comprehensive system health.

**Fix pattern** (based on previous fix):
```python
# Before (causes UnboundLocalError)
except Exception as e:
    # Missing variable initialization
    pass

# After (properly initializes defaults)
except Exception as e:
    variable_name = default_value
    another_variable = another_default
```
</implementation>

<output>
Modify `./api_server.py`:
- Fix the `/api/server/status` endpoint to return valid responses
- Add proper exception handling with variable initialization
- Ensure all variables are defined before use
- Add defensive null checks if needed
</output>

<verification>
Before declaring complete, verify your fix:

1. **Check server logs** for the actual error:
   ```bash
   # The error traceback should be visible in the running server process
   ```

2. **Test the endpoint** after fixing:
   ```bash
   curl http://localhost:8000/api/server/status
   ```
   Should return valid JSON, NOT "Internal Server Error"

3. **Compare with working endpoint**:
   ```bash
   curl http://localhost:8000/api/status
   ```
   Both should return successful responses now

Success criteria:
- `/api/server/status` returns 200 OK with valid JSON
- No 500 errors in browser console
- Server logs show no exceptions for this endpoint
- Frontend can successfully fetch server status
</verification>

<success_criteria>
✅ The `/api/server/status` endpoint returns 200 OK
✅ Valid JSON response is returned (not error message)
✅ No exceptions in server logs
✅ Frontend successfully fetches server status without retries
✅ Browser console shows no more 500 errors for this endpoint
</success_criteria>
