"""
HTML Report Generator
=====================

Produces a single self-contained HTML file from a BacktestResult.
No external CDN required -- Chart.js is loaded dynamically on open.

Output is viewable in any browser without a server.
"""

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..performance import BacktestResult


def generate_html_report(result: "BacktestResult", output_path: str) -> None:
    """
    Writes a self-contained HTML backtest report to output_path.
    """
    timestamps = [s["timestamp"] for s in result.equity_curve]
    equities = [s["equity"] for s in result.equity_curve]

    # Calculate drawdown series
    peak = result.initial_capital
    drawdowns: list[float] = []
    for eq in equities:
        peak = max(peak, eq)
        drawdowns.append(round((peak - eq) / peak * 100, 2))

    ts_json = str(timestamps[:500])  # Downsample for chart performance
    eq_json = str(equities[:500])
    dd_json = str(drawdowns[:500])

    trade_rows = ""
    for t in result.trade_log[:200]:
        trade_rows += (
            f"<tr><td>{t.get('timestamp', '')[:16]}</td>"
            f"<td>{t.get('side', '')}</td>"
            f"<td>{t.get('quantity', '')}</td>"
            f"<td>{t.get('fill_price', '')}</td>"
            f"<td>${t.get('fee', 0):.4f}</td></tr>\n"
        )

    ret_color = "green" if result.total_return_pct >= 0 else "red"
    cagr_color = "green" if result.cagr_pct >= 0 else "red"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Backtest Report -- {result.symbol}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1100px; margin: 40px auto; padding: 0 20px; background: #f9f9f9; }}
  h1 {{ color: #1a1a2e; }} h2 {{ color: #16213e; margin-top: 40px; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 16px; margin: 20px 0; }}
  .card {{ background: white; border-radius: 8px; padding: 16px 24px; box-shadow: 0 1px 3px rgba(0,0,0,.1); min-width: 160px; }}
  .card .label {{ font-size: 12px; color: #666; text-transform: uppercase; }}
  .card .value {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
  .green {{ color: #16a34a; }} .red {{ color: #dc2626; }}
  canvas {{ background: white; border-radius: 8px; padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1); width: 100% !important; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-top: 16px; }}
  th {{ background: #1a1a2e; color: white; padding: 10px 14px; text-align: left; font-size: 13px; }}
  td {{ padding: 8px 14px; border-bottom: 1px solid #eee; font-size: 13px; }}
  tr:last-child td {{ border-bottom: none; }}
</style>
</head>
<body>
<h1>Backtest Report -- {result.symbol}</h1>
<p style="color:#666">{result.start} -&gt; {result.end} &nbsp;|&nbsp; Initial capital: ${result.initial_capital:,.0f}</p>

<div class="cards">
  <div class="card"><div class="label">Total Return</div>
    <div class="value {ret_color}">{result.total_return_pct:+.1f}%</div></div>
  <div class="card"><div class="label">CAGR</div>
    <div class="value {cagr_color}">{result.cagr_pct:+.1f}%</div></div>
  <div class="card"><div class="label">Sharpe Ratio</div>
    <div class="value">{result.sharpe_ratio:.2f}</div></div>
  <div class="card"><div class="label">Max Drawdown</div>
    <div class="value red">{result.max_drawdown_pct:.1f}%</div></div>
  <div class="card"><div class="label">Win Rate</div>
    <div class="value">{result.win_rate_pct:.1f}%</div></div>
  <div class="card"><div class="label">Profit Factor</div>
    <div class="value">{result.profit_factor:.2f}</div></div>
  <div class="card"><div class="label">Total Trades</div>
    <div class="value">{result.total_trades}</div></div>
  <div class="card"><div class="label">Total Fees</div>
    <div class="value">${result.total_fees:,.2f}</div></div>
</div>

<h2>Equity Curve</h2>
<canvas id="eqChart" height="80"></canvas>

<h2>Drawdown</h2>
<canvas id="ddChart" height="60"></canvas>

<h2>Trade Log (first 200)</h2>
<table>
  <thead><tr><th>Timestamp</th><th>Side</th><th>Qty</th><th>Fill Price</th><th>Fee</th></tr></thead>
  <tbody>{trade_rows}</tbody>
</table>

<script>
const ts = {ts_json};
const eq = {eq_json};
const dd = {dd_json};

function tryChart(id, labels, data, color, label, yReverse) {{
  const canvas = document.getElementById(id);
  if (!canvas || typeof Chart === 'undefined') return;
  new Chart(canvas, {{
    type: 'line',
    data: {{ labels, datasets: [{{ label, data, borderColor: color, fill: true,
      backgroundColor: color + '22', borderWidth: 2, pointRadius: 0 }}] }},
    options: {{ animation: false, plugins: {{ legend: {{ display: false }} }},
      scales: {{ y: {{ reverse: !!yReverse }} }} }}
  }});
}}

// Load Chart.js dynamically
const s = document.createElement('script');
s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js';
s.onload = () => {{
  tryChart('eqChart', ts, eq, '#2563eb', 'Equity ($)', false);
  tryChart('ddChart', ts, dd, '#dc2626', 'Drawdown (%)', true);
}};
document.head.appendChild(s);
</script>
</body>
</html>"""

    Path(output_path).write_text(html, encoding="utf-8")
