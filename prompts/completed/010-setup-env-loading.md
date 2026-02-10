<objective>
Configure the trading bot server to properly load environment variables from the provided .env file, enabling real data fetching from the Pacifica test network. This setup is essential for validating the database schema with authentic API response structures and ensuring production-ready data accuracy.
</objective>

<context>
This is for the Unity Oracle Aggregator trading bot project, Phase 3.5: Database Configuration & Testing. The system uses Python with FastAPI and needs to connect to Pacifica test network using credentials in the .env file. The .env file contains valid credentials that have been used before and must not be modified.

Who will use this: The development team for testing and validating database schema with real data.
What it's for: Enabling secure credential loading to fetch authentic trading data for schema validation.
</context>

<requirements>
Configure the system to load environment variables from trading_bot_v2/.env with the following:
1. Update config.py to properly load all environment variables from the .env file
2. Ensure the Pacifica client uses the loaded credentials for test network connections
3. Verify that real API data can be fetched without exposing credentials in code
4. Maintain security by keeping credentials out of version control and code

Be explicit about using python-dotenv or similar library for .env loading.
</requirements>

<constraints>
- Do not modify the .env file in any way - it contains valid, working credentials
- Ensure credentials are never logged, printed, or exposed in error messages
- Use secure environment variable loading practices
- Maintain compatibility with existing code structure
- Do not hardcode any credential values in the codebase
</constraints>

<implementation>
Follow these secure implementation patterns:
- Use python-dotenv to load .env file at application startup
- Load environment variables before importing modules that depend on them
- Validate that required credentials are present before proceeding
- Handle missing .env file gracefully with clear error messages (without exposing credentials)

Avoid security risks:
- Never commit .env files to version control
- Never log or print credential values
- Use environment variables for all sensitive configuration
</implementation>

<output>
Create/modify files with relative paths:
- `./trading_bot_v2/config.py` - Update to load environment variables from .env file securely
</output>

<verification>
Before declaring complete, verify your work:
- Environment variables load correctly from .env file
- Pacifica client can authenticate and fetch real data from test network
- No credentials are exposed in logs or error messages
- Application starts successfully with loaded configuration
- Real API responses match expected structures for database schema validation
</verification>

<success_criteria>
- Config.py successfully loads all environment variables from .env file
- Real data fetching works with Pacifica test network credentials
- No security vulnerabilities introduced (credentials not exposed)
- Database schema can be validated with authentic API response structures
- System ready for Phase 3.5 database implementation with real data
</success_criteria></content>
<parameter name="filePath">./prompts/010-setup-env-loading.md