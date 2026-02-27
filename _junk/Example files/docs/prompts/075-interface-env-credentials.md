<objective>
Make the trading bot interface automatically load credentials from the environment file for the default profile. Currently, users need to manually enter credentials, but the interface should automatically populate these from environment variables when the page loads.
</objective>

<context>
This is for a Solana-based perpetual trading bot with an HTML interface that connects to a FastAPI backend. The interface currently requires manual credential entry, but should automatically load credentials from environment variables (.env file) for the default profile to improve user experience and reduce setup friction.

Key files to examine:
@trading_bot_interface.html - Main interface file where credential loading should be implemented
@api_server.py - Backend API that handles authentication and profile management
@.env.example - Shows what environment variables should be available
</context>

<requirements>
Implement automatic credential loading by:
1. Reading environment variables from the browser (using a secure method)
2. Populating the default profile form fields with these credentials
3. Ensuring credentials are loaded when the page initializes
4. Providing fallback behavior if environment variables are not available
5. Maintaining security by not exposing sensitive data in client-side logs

The interface should automatically fill in:
- Account public key
- Agent wallet private key
- Pacifica environment (testnet/mainnet)
- Any other relevant authentication credentials
</requirements>

<implementation>
Use JavaScript to load environment variables and populate form fields on page load. Consider these approaches:

1. **Environment Variable Access**: Use a secure method to access environment variables (may require API endpoint or build-time injection)
2. **Form Population**: Automatically fill form fields with loaded credentials
3. **Default Profile**: Target the default profile specifically, not all profiles
4. **Error Handling**: Gracefully handle missing environment variables
5. **Security**: Ensure credentials are not logged or exposed in client-side code

Avoid storing credentials in localStorage or sessionStorage for security reasons.
</implementation>

<output>
Modify the trading bot interface to automatically load credentials:
- Update `./trading_bot_interface.html` with JavaScript to load and populate credentials from environment variables
- Ensure the default profile form is automatically filled on page load
- Add appropriate error handling for missing environment variables
</output>

<verification>
Test the credential loading by:
- Opening the interface in a browser
- Verifying that default profile fields are automatically populated
- Checking that credentials match the .env file values
- Confirming that manual credential entry still works as fallback
- Ensuring no sensitive data appears in browser console logs
</verification>

<success_criteria>
Credential loading is successful when:
- Interface automatically populates default profile fields on page load
- Credentials match those in the .env file
- No manual entry required for basic authentication
- Fallback to manual entry works if environment variables are missing
- No security vulnerabilities introduced (no credential exposure in logs)
</success_criteria></content>
<parameter name="filePath">prompts/075-interface-env-credentials.md