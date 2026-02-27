<objective>
Validate that the testnet is working properly, including connectivity, trade execution capabilities, API connections alignment, and optimal API source usage. This ensures the bot can safely operate on testnet before real funds.
</objective>

<context>
Testnet validation for the Pacifica trading bot. Focus on:
- Connecting to testnet APIs
- Executing test trades
- Verifying API connections match database configurations
- Ensuring use of best/recommended API sources per docs
Reference python-sdk/, docs/, and context files/pacifica/ for proper API usage.
</context>

<requirements>
1. Check testnet configuration and environment variables
2. Test connectivity to Pacifica testnet APIs
3. Execute sample test trades (paper trading only)
4. Verify all API connections and database names align
5. Confirm use of optimal API sources as documented
</requirements>

<validation>
Run actual connection tests and trade simulations. Document any connection failures, misalignments, or suboptimal configurations.
</validation>

<output>
Save validation results to: ./docs/testnet-validation-report.md
Include test results, any issues found, and fixes applied.
</output>

<verification>
Before completing, verify:
- All connection tests have been run
- Trade executions were successful (paper trades)
- API and database alignments are confirmed
</verification>

<success_criteria>
Testnet is fully operational with all connections working and optimal API sources in use.
</success_criteria>