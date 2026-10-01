# Historical audit artifacts

Full methodology, commands, paired results and limitations are in [the historical rerun report](../../docs/backtesting-rerun-2026-09-16.md).

Eight paired cases (16 runs) plus one deterministic repaired-run repeat completed offline. `comparison.json` summarizes the paired results. The first `prefinal_*` diagnostic run is retained separately and excluded from the comparison.

Use `python out/backtest_audit_20260916/run_historical.py --help` from the repository root. A rerun reuses its output filename unless `--suffix` is supplied. The script blocks socket connections and disables market-data downloads. It does not start the bot or contact trading accounts.
