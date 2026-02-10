<objective>
Deploy the trading bot to testnet environment with valid credentials, enabling actual trading operations while using test funds.
This final step transitions the bot from development/demo mode to live testnet operation, validating the complete system in a real trading environment.
</objective>

<context>
The bot has been rebuilt with real implementations and tested for integration. Now we need to deploy it with proper testnet credentials to enable actual trading.
Testnet allows practicing with fake funds while using real market conditions and API behavior.
Ensure proper security: credentials in environment variables, no hardcoded secrets.
Reference deployment documentation in README.md.
</context>

<requirements>
1. Configure environment with valid Pacifica testnet credentials
2. Set up proper database path for testnet operations
3. Start the trading bot with real trading enabled
4. Monitor initial operations and trading activity
5. Verify position management and risk controls work in live environment
6. Document deployment process and initial results
7. Set up basic monitoring for ongoing operations
</requirements>

<implementation>
- Create .env file with testnet credentials (API_KEY, SECRET_KEY, BASE_URL for testnet)
- Configure database path for testnet data
- Start API server: `python -m trading_bot_v2.api_server`
- Start trading bot: `python -m trading_bot_v2.trading_bot`
- Monitor logs and web interface for proper operation
- Test basic trading operations (if bot is configured to trade)
- Verify data persistence and position tracking
- Document any issues and resolutions
</implementation>

<output>
Create deployment documentation:
- `./deployment/testnet-deployment.md` - Document setup process, credentials configuration, and initial operation results
Update if needed:
- `./trading_bot_v2/README.md` - Add testnet deployment instructions
</output>

<verification>
Before declaring complete:
1. System starts with testnet credentials
2. API server serves real testnet data
3. Trading bot initializes and begins operations
4. Database persists testnet trading data
5. Web interface shows live testnet information
6. No authentication or connection errors
</verification>

<success_criteria>
- Bot successfully connects to testnet
- Real trading operations execute (with test funds)
- All components work in live environment
- Data persistence confirmed
- No security issues or credential exposure
- Deployment process documented
- System stable for extended testnet operation
</success_criteria>