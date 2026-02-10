<objective>
Update the api_server.py file to replace the mock PacificaClient with the real implementation, enabling actual API communication with Pacifica.fi while maintaining the existing FastAPI endpoints and error handling.
This is critical for transitioning from demo mode to real trading capability, allowing the bot to fetch live market data and execute trades.
</objective>

<context>
This is part of a trading bot rebuild for Pacifica.fi perpetual futures. The project uses FastAPI for the web interface, with a modular architecture separating API client, trading logic, and database layers.
The current api_server.py uses a mock client that returns hardcoded responses. The real PacificaClient is already implemented in pacifica_client.py with HMAC authentication.
Examine these files for integration points:
@trading_bot_v2/api_server.py
@trading_bot_v2/pacifica_client.py
@trading_bot_v2/config.py
</context>

<requirements>
1. Import the real PacificaClient instead of the mock version
2. Initialize the client with proper configuration from environment variables
3. Update all endpoint handlers to use real API calls instead of mock responses
4. Maintain existing error handling and response formats
5. Ensure the server can start and serve endpoints without breaking existing functionality
6. Handle authentication errors gracefully (invalid credentials, network issues)
</requirements>

<implementation>
- Replace `from .mock_client import MockPacificaClient` with `from .pacifica_client import PacificaClient`
- Update client instantiation to use real config: `client = PacificaClient(api_key=config.API_KEY, secret_key=config.SECRET_KEY, base_url=config.BASE_URL)`
- For each endpoint, replace mock method calls with real client methods
- Preserve the exact response format and status codes
- Add proper exception handling for API errors, converting them to appropriate HTTP responses
- Do not modify endpoint signatures or add new endpoints - only change the implementation
</implementation>

<output>
Modify the existing file:
- `./trading_bot_v2/api_server.py` - Update imports and client usage throughout the file
</output>

<verification>
Before declaring complete:
1. Run `python -m trading_bot_v2.api_server` and verify the server starts without import errors
2. Test each endpoint with curl or a browser to ensure they return proper responses (even if using test credentials)
3. Check that error handling works for invalid credentials or network issues
4. Confirm the response format matches the existing mock responses
</verification>

<success_criteria>
- Server starts successfully with real client
- All endpoints return responses in the expected format
- No breaking changes to the API interface
- Proper error handling for authentication and network issues
- Code passes basic syntax and import checks
</success_criteria>