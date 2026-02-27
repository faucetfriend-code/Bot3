<objective>
Fix the syntax errors and undefined variables in api_server.py that are preventing the API server from starting properly. This is critical because the HTML interface cannot connect to the backend, resulting in ERR_CONNECTION_REFUSED errors for all API endpoints.
</objective>

<context>
This is for the FastAPI-based trading bot API server. Recent changes introduced multiple syntax errors including indentation issues, undefined variables, and structural problems that prevent the server from starting. The HTML interface shows connection refused errors because the server cannot run with these syntax errors.

@api_server.py - contains multiple syntax errors that need fixing
</context>

<requirements>
1. Fix all indentation errors and structural issues in api_server.py
2. Resolve all undefined variable references
3. Ensure proper function definitions and return statements
4. Verify the server can start without syntax errors
5. Test that API endpoints respond correctly
</requirements>

<implementation>
Thoroughly analyze the syntax errors shown in the diagnostics. Fix indentation issues, undefined variables, and structural problems. For maximum efficiency, whenever you need to perform multiple independent operations, invoke all relevant tools simultaneously rather than sequentially.

Go beyond basic fixes - ensure the code is properly structured and all variables are correctly defined and scoped.

After receiving tool results, carefully reflect on their quality and determine optimal next steps before proceeding.
</implementation>

<output>
Modify the following files with relative paths:
- ./api_server.py - fix all syntax errors, indentation issues, and undefined variables
</output>

<verification>
Before declaring complete:
1. Run syntax check on api_server.py to ensure no errors
2. Attempt to start the API server with `python api_server.py`
3. Verify the server starts without errors and listens on port 8000
4. Test basic API endpoints to ensure they respond
</verification>

<success_criteria>
- api_server.py has no syntax errors
- API server starts successfully with `python api_server.py`
- Server responds to HTTP requests on localhost:8000
- HTML interface can connect and load data without ERR_CONNECTION_REFUSED
</success_criteria>