# Max Profit Walk-Forward Results

Train Period: 2021-01-01 to 2021-07-01 (6m)
OOS Periods: 2021-07-01 to 2024-01-01 (3m rolling)

Constraints: Sharpe > 1.0, P(loss) < 10.0%, Trades >= 50

## Top 10 Configurations Ranked by OOS Return

| Rank | SD Thresh | ATR Mult | RR | SFP | Volume | Long | Return% | Sharpe | P(Loss)% | Trades | Status |
|-----|-----------|----------|-----|-----|--------|------|---------|--------|----------|--------|--------|
| 1 | 2.5 | 1.0 | 3.0 | False | True | False | -24.4 | -2.88 | 68.6 | 70 | FAIL |
| 2 | 2.5 | 1.0 | 2.0 | False | True | False | -25.6 | -4.06 | 70.0 | 70 | FAIL |
| 3 | 2.5 | 1.0 | 2.5 | False | True | False | -26.0 | -3.41 | 70.0 | 70 | FAIL |