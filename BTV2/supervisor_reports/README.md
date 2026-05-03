# Supervisor Reports

Append-only daily reports written by the Claude supervisor routine.

## Format

- One file per UTC day: `YYYY-MM-DD.md`
- Each check appends a section, headed by ISO-8601 timestamp
- Reports are markdown and human-readable

## Structure of each check section

```markdown
## 2026-05-02T08:00:00Z

**Health:** ok / unhealthy / not-running
**Balance:** $1,042.50 (peak $1,042.50, dd 0.0%)
**Trades since last check:** 2 (1 win, 1 loss; net +$3.20)
**Open positions:** 1 (BTC long, age 2h)
**Active alerts:** none

### Per-strategy
- Mean Reversion (1d): 1 trade, +$5.10 (calm regime — expected)
- MA Crossover (4h): 1 trade, -$1.90 (bull regime — expected)
- Liquidation Capture: silent (expected — fires <10×/year)
- Martingale MR: silent (expected — calm regime)

### Anomalies / notes
- (none) or "Investigated stuck order BTC-USDC, OK"

### Next check: 2026-05-02T12:00:00Z (4h cadence — dd within 0–3%)
```

## Retention

No automatic deletion. Old reports are kept for trend analysis.
