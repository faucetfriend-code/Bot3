<objective>
Start the API server and verify it's running correctly. The frontend is showing ERR_CONNECTION_REFUSED because the backend server isn't running on port 8000.
</objective>

<context>
The browser console shows multiple connection refused errors to `http://localhost:8000`. This is expected - the API server needs to be started manually.

**Error Pattern:**
```
:8000/api/status:1  Failed to load resource: net::ERR_CONNECTION_REFUSED
:8000/api/pacifica/markets:1  Failed to load resource: net::ERR_CONNECTION_REFUSED
```

This means the frontend HTML is loaded, but the Python backend (api_server.py) isn't running.
</context>

<solution>
## Simple Fix: Start the Server

### Option 1: Start in Current Terminal
```bash
python api_server.py
```

**You should see:**
```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

### Option 2: Start in Background (Windows)
```bash
start python api_server.py
```

### Option 3: Check if Server is Already Running
```bash
# Windows - Check if port 8000 is in use
netstat -ano | findstr :8000

# If something is using it, kill the process
taskkill /PID <PID> /F

# Then start the server
python api_server.py
```

### Option 4: Check for Python Errors
If the server won't start, check for errors:
```bash
python -m py_compile api_server.py
# Should complete without errors

python api_server.py
# Watch for error messages
```
</solution>

<verification>
After starting the server:

1. **Check server logs** - Should see "Uvicorn running on http://0.0.0.0:8000"
2. **Check frontend** - Refresh browser (Ctrl+F5)
3. **Test API** - Open new terminal:
   ```bash
   curl http://localhost:8000/api/status
   ```
   Should return JSON like: `{"success": true, ...}`

4. **Check browser console** - Should see successful API calls, no more ERR_CONNECTION_REFUSED
</verification>

<common_issues>
### Issue 1: Port 8000 Already in Use
**Symptom:** "Address already in use" error

**Fix:**
```bash
# Find what's using port 8000
netstat -ano | findstr :8000

# Kill it
taskkill /PID <PID> /F

# Try again
python api_server.py
```

### Issue 2: Module Not Found
**Symptom:** "No module named 'fastapi'" or similar

**Fix:**
```bash
pip install -r requirements.txt
```

### Issue 3: Database Locked
**Symptom:** "database is locked" error

**Fix:**
```bash
# Check for stale WAL files
ls data/*.db-*

# If exists, remove them (while server is stopped)
rm data/*.db-shm data/*.db-wal

# Or on Windows
del data\*.db-shm data\*.db-wal

# Try starting server again
python api_server.py
```

### Issue 4: Python Not Found
**Symptom:** "'python' is not recognized"

**Fix:**
```bash
# Try python3
python3 api_server.py

# Or use full path
C:\Python\python.exe api_server.py
```
</common_issues>

<success_criteria>
Server is running successfully when:

1. ✅ Terminal shows "Uvicorn running on http://0.0.0.0:8000"
2. ✅ No error messages in server console
3. ✅ `curl http://localhost:8000/api/status` returns JSON
4. ✅ Browser console shows successful API calls (no ERR_CONNECTION_REFUSED)
5. ✅ Frontend displays data (bot status, positions, market data)
</success_criteria>

<expected_output>
When everything is working, you should see:

**Server Console:**
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:xxxxx - "GET /api/status HTTP/1.1" 200 OK
INFO:     127.0.0.1:xxxxx - "GET /api/pacifica/markets HTTP/1.1" 200 OK
```

**Browser Console:**
```
✅ TradingBotUI available, loading profiles
✅ Global function references set
API server detected and responding
Market data loaded successfully
```

**Frontend:**
- Bot status shows "Standby" or current state
- No red error messages
- Data panels populate with information
</expected_output>

<notes>
- The server must stay running for the frontend to work
- Ctrl+C stops the server
- Each time you close the terminal, you'll need to restart the server
- The ERR_CONNECTION_REFUSED errors are **normal** when the server isn't running
- This isn't a bug - it's expected behavior for a web application
</notes>
