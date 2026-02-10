<objective>
Fix bot stability issues causing premature stopping after activation. This ensures the trading bot runs continuously without unexpected shutdowns, maintaining trading operations and data collection reliability.
</objective>

<context>
This is for a Solana perpetuals trading bot that needs to run 24/7 for automated trading. Users depend on the bot staying active for position management, data collection, and strategy execution. Premature stopping disrupts trading performance and requires manual intervention.

@main.py - examine bot startup and main loop logic
@process_manager.py - check process management and monitoring
@execution.py - review trade execution and error handling
@config.py - verify configuration settings that might cause early termination
@monitoring.py - check health monitoring and restart logic
</context>

<requirements>
1. Identify why the bot stops shortly after activation
2. Implement proper error handling to prevent crashes from stopping the bot
3. Add health checks and automatic recovery mechanisms
4. Ensure the bot handles connection issues gracefully without shutting down
5. Implement logging for shutdown causes and restart capabilities
</requirements>

<implementation>
Thoroughly analyze the bot's lifecycle and identify failure points. Look for:
- Unhandled exceptions in main loops causing crashes
- Missing error recovery in trading execution
- Improper connection handling leading to shutdowns
- Configuration issues causing early termination
- Missing process monitoring and restart logic

Go beyond basic fixes - implement comprehensive error boundaries, graceful degradation, and automatic restart capabilities. Ensure the bot can recover from transient failures without human intervention.

For stability, add watchdog timers, health checks, and proper cleanup on shutdown.
</implementation>

<output>
Modify the following files with relative paths:
- ./main.py - fix main bot loop and error handling
- ./process_manager.py - implement proper process monitoring and restart logic
- ./execution.py - add error recovery in trade execution
- ./monitoring.py - enhance health checks and failure detection
- ./config.py - adjust configuration for better stability (if needed)
</output>

<verification>
Before declaring complete:
1. Start the bot and monitor for at least 30 minutes to ensure it doesn't stop prematurely
2. Test error scenarios (network disconnection, API failures) to verify graceful handling
3. Check logs for any error messages or shutdown causes
4. Verify automatic restart works if the bot does encounter issues
5. Confirm the bot maintains connections and continues data collection
</verification>

<success_criteria>
- Bot runs continuously for extended periods without unexpected shutdowns
- Errors are logged but don't cause bot termination
- Automatic recovery from transient failures
- Clear logging of any issues that occur
- Process monitoring detects and handles failures appropriately
</success_criteria>