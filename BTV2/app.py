"""
Walk-Forward Trading Analysis Dashboard  v2
============================================
Strategies : Mean Reversion · VWAP Scalping · Momentum Scalping ·
             Liquidation Capture · Grid Trading · MA Crossover
Pre-filter : 2-pole causal Butterworth (scipy.signal.lfilter — zero lookahead)
Costs      : 0.10% fee + 0.05% slippage per side (0.30% round-trip)
Engine     : 12-month rolling train → 3-month blind test; Sharpe-optimised params
Agent      : Coordinate-descent .env optimiser (reads/writes Bot3/.env)

Run:
    cd BTV2
    pip install -r requirements.txt
    streamlit run app.py
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import date

# Local modules (same directory)
from strategies import (
    STRATEGY_REGISTRY,
    STRATEGY_NOTES,
    TRAIN_MONTHS,
    TEST_MONTHS,
    build_windows,
    compute_metrics,
    optimize_strategy,
    stitch_oos_equity,
)
from agent import (
    read_env_params,
    read_env_display,
    run_parameter_sweep,
    build_proposal,
    apply_to_env,
    ENV_PATH,
)

# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Walk-Forward Analysis v2",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

STRATEGY_NAMES = list(STRATEGY_REGISTRY.keys())

# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING  — returns full OHLCV DataFrame
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_data(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Download adjusted OHLCV via yfinance."""
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df.empty:
        return pd.DataFrame()
    # Flatten MultiIndex columns (newer yfinance versions)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    return df.dropna()


# ─────────────────────────────────────────────────────────────────────────────
# DISPLAY HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def fmt_val(key: str, val: float) -> str:
    if key == "n_trades":
        return str(int(val))
    if key == "profit_factor":
        return "∞" if val == float("inf") else f"{val:.2f}"
    if key in ("total_return_pct", "cagr_pct", "win_rate_pct", "max_dd_pct"):
        return f"{val:.1f}%"
    return f"{val:.2f}"


def fmt_delta(key: str, delta: float) -> str:
    if key == "n_trades":
        return f"{int(delta):+d}"
    if key == "profit_factor" and delta == float("inf"):
        return "+∞"
    if key in ("total_return_pct", "cagr_pct", "win_rate_pct", "max_dd_pct"):
        return f"{delta:+.1f}%"
    return f"{delta:+.2f}"


METRICS_DEF = [
    ("total_return_pct", "Total Return"),
    ("cagr_pct",         "CAGR"),
    ("sharpe",           "Sharpe Ratio"),
    ("max_dd_pct",       "Max Drawdown"),
    ("win_rate_pct",     "Win Rate"),
    ("profit_factor",    "Profit Factor"),
    ("n_trades",         "# Trades"),
]

# ─────────────────────────────────────────────────────────────────────────────
# MAIN UI
# ─────────────────────────────────────────────────────────────────────────────
st.title("📈 Walk-Forward Trading Analysis  v2")
st.caption(
    "**Filter:** 2-pole causal Butterworth (lfilter, zero lookahead bias)  ·  "
    "**Costs:** 0.10% fee + 0.05% slippage per side  ·  "
    "**Engine:** 12-month Sharpe-optimised train → 3-month blind test"
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")

    ticker     = st.text_input("Ticker", value="BTC-USD")
    start_date = st.date_input("Start Date", value=date(2018, 1, 1),
                               min_value=date(2010, 1, 1))
    end_date   = st.date_input("End Date",   value=date.today())

    st.divider()

    strategy_name = st.selectbox(
        "Strategy",
        STRATEGY_NAMES,
        index=0,
        help="Select one of the 6 live trading bot strategies to backtest.",
    )
    st.caption(f"_{STRATEGY_NOTES[strategy_name]}_")

    st.divider()

    butterworth_cutoff = st.slider(
        "Butterworth Cutoff Frequency",
        min_value=0.05, max_value=0.25, value=0.10, step=0.01,
        help=(
            "Normalised frequency [0,1]. Lower = more smoothing.  "
            "Applied to Close via lfilter (causal, forward-only) before all indicators.  "
            "0.10 ≈ 10-bar effective filter on daily data."
        ),
    )

    st.divider()

    with st.expander("🔬 Walk-Forward Settings", expanded=False):
        st.write(f"**Train window:** {TRAIN_MONTHS} months")
        st.write(f"**Test window:**  {TEST_MONTHS} months")
        st.write("**Step:**          3 months (non-overlapping OOS windows)")
        st.write("**Objective:**     Maximise Sharpe on training window")
        _, grid, defaults = STRATEGY_REGISTRY[strategy_name]
        total_combos = 1
        for v in grid.values():
            total_combos *= len(v)
        st.write(f"**Grid combos:**   {total_combos} per fold")

    st.divider()
    run_btn = st.button("▶  Run Analysis", type="primary", use_container_width=True)


# ── Pre-run state ─────────────────────────────────────────────────────────────
if not run_btn:
    st.info(
        "👈  Select a strategy and configure the date range in the sidebar, "
        "then click **Run Analysis** to begin."
    )
    st.stop()


# ─────────────────────────────────────────────────────────────────────────────
# DATA DOWNLOAD
# ─────────────────────────────────────────────────────────────────────────────
with st.spinner(f"Downloading {ticker} OHLCV data…"):
    df = load_data(ticker, str(start_date), str(end_date))

if df.empty or "Close" not in df.columns:
    st.error(f"No data returned for **{ticker}**. Check ticker symbol and date range.")
    st.stop()

st.success(
    f"✅  Loaded **{len(df):,}** daily bars — "
    f"{df.index[0].date()} → {df.index[-1].date()}"
)


# ─────────────────────────────────────────────────────────────────────────────
# BUILD WINDOWS
# ─────────────────────────────────────────────────────────────────────────────
windows = build_windows(start_date, end_date)

if not windows:
    st.error(
        "Not enough data to build a walk-forward window. "
        f"Extend the date range to cover at least {TRAIN_MONTHS + TEST_MONTHS} months."
    )
    st.stop()

st.write(
    f"Strategy: **{strategy_name}**  ·  "
    f"Walk-forward: **{len(windows)} folds** "
    f"({TRAIN_MONTHS}-month train → {TEST_MONTHS}-month blind test)"
)


# ─────────────────────────────────────────────────────────────────────────────
# WALK-FORWARD  (live progress bar)
# ─────────────────────────────────────────────────────────────────────────────
st.subheader("⏳ Walk-Forward Processing")
progress_bar = st.progress(0.0, text="Starting…")
status_text  = st.empty()

func, _, defaults = STRATEGY_REGISTRY[strategy_name]

oos_segments:   list = []
oos_trades_all: list[float] = []
fold_results:   list[dict]  = []

for w in windows:
    fold_i      = w["fold"]
    total_folds = len(windows)

    df_train = df.loc[str(w["train_start"]) : str(w["train_end"])]
    df_test  = df.loc[str(w["test_start"])  : str(w["test_end"])]

    if len(df_train) < 50 or len(df_test) < 10:
        progress_bar.progress(fold_i / total_folds,
                              text=f"Fold {fold_i}/{total_folds} — skipped (insufficient data)")
        continue

    # Optimise params on training window
    best_params = optimize_strategy(df_train, butterworth_cutoff, strategy_name)

    # Evaluate on blind test window
    oos_eq, oos_trd = func(df_test, butterworth_cutoff, **best_params)
    oos_segments.append(oos_eq)
    oos_trades_all.extend(oos_trd)

    fold_m = compute_metrics(oos_eq, oos_trd)
    fold_results.append({
        "Fold"         : fold_i,
        "Test Period"  : f"{w['test_start']} → {w['test_end']}",
        **{k: best_params[k] for k in best_params},
        "OOS Return %" : round(fold_m["total_return_pct"], 2),
        "OOS Sharpe"   : round(fold_m["sharpe"], 2),
        "OOS Max DD %" : round(fold_m["max_dd_pct"], 2),
        "OOS Trades"   : fold_m["n_trades"],
    })

    progress_bar.progress(
        fold_i / total_folds,
        text=(
            f"Fold {fold_i}/{total_folds}  ·  "
            f"Params: {best_params}  ·  "
            f"OOS return: {fold_m['total_return_pct']:+.1f}%"
        ),
    )

progress_bar.progress(1.0, text="✅  All folds complete!")
status_text.empty()


# ─────────────────────────────────────────────────────────────────────────────
# IN-SAMPLE BASELINE  (default .env params, full period)
# ─────────────────────────────────────────────────────────────────────────────
with st.spinner(f"Running in-sample baseline ({strategy_name}, default params)…"):
    is_equity, is_trades = func(df, butterworth_cutoff, **defaults)

oos_equity_full = stitch_oos_equity(oos_segments)
is_metrics      = compute_metrics(is_equity,      is_trades)
oos_metrics     = compute_metrics(oos_equity_full, oos_trades_all)

st.success("Analysis complete.")
st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# SECTION A — GANTT CHART
# ═════════════════════════════════════════════════════════════════════════════
st.subheader("📅 Walk-Forward Window Map")

gantt_rows = []
for w in windows:
    gantt_rows += [
        dict(Fold=f"Fold {w['fold']:02d}", Start=pd.Timestamp(w["train_start"]),
             Finish=pd.Timestamp(w["train_end"]),  Type="Training"),
        dict(Fold=f"Fold {w['fold']:02d}", Start=pd.Timestamp(w["test_start"]),
             Finish=pd.Timestamp(w["test_end"]),   Type="Testing"),
    ]

gantt_df  = pd.DataFrame(gantt_rows)
fig_gantt = px.timeline(
    gantt_df, x_start="Start", x_end="Finish", y="Fold", color="Type",
    color_discrete_map={"Training": "#1f77b4", "Testing": "#ff7f0e"},
    title="Training Windows (Blue) · Blind Test Windows (Orange)",
)
fig_gantt.update_yaxes(autorange="reversed", tickfont=dict(size=10))
fig_gantt.update_xaxes(title_text="Date")
fig_gantt.update_layout(
    height    =max(380, len(windows) * 22 + 100),
    showlegend=True,
    legend    =dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    margin    =dict(l=80, r=20, t=70, b=30),
    template  ="plotly_white",
)
st.plotly_chart(fig_gantt, use_container_width=True)

st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# SECTION B — SIDE-BY-SIDE METRICS
# ═════════════════════════════════════════════════════════════════════════════
st.subheader("📊 Performance Metrics Comparison")

col1, col2 = st.columns(2, gap="large")

with col1:
    st.markdown(
        f"#### 🔵 {strategy_name} — Default Params (In-Sample)\n"
        "*Default .env parameters applied to full date range — no optimisation*"
    )
    for key, label in METRICS_DEF:
        st.metric(label=label, value=fmt_val(key, is_metrics[key]))

with col2:
    st.markdown(
        f"#### 🟠 {strategy_name} — Walk-Forward OOS\n"
        "*Params Sharpe-optimised per fold — blind test windows stitched*"
    )
    for key, label in METRICS_DEF:
        val   = oos_metrics[key]
        delta = val - is_metrics[key]
        st.metric(
            label=label,
            value=fmt_val(key, val),
            delta=fmt_delta(key, delta) + " vs IS",
            delta_color="normal",
        )

st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# SECTION C — EQUITY CURVES
# ═════════════════════════════════════════════════════════════════════════════
st.subheader("📈 Equity Curve Comparison")

fig_eq = go.Figure()

# Default in-sample (blue)
fig_eq.add_trace(go.Scatter(
    x=is_equity.index, y=is_equity.values,
    name=f"🔵 {strategy_name} — Default Params (IS)",
    line=dict(color="#1f77b4", width=2),
    hovertemplate="%{x|%Y-%m-%d}  ×%{y:.4f}<extra></extra>",
))

# Walk-forward OOS (orange)
if not oos_equity_full.empty:
    fig_eq.add_trace(go.Scatter(
        x=oos_equity_full.index, y=oos_equity_full.values,
        name=f"🟠 {strategy_name} — Walk-Forward OOS",
        line=dict(color="#ff7f0e", width=2),
        hovertemplate="%{x|%Y-%m-%d}  ×%{y:.4f}<extra></extra>",
    ))

# BTC buy-and-hold reference (grey dotted)
btc_bh = df["Close"] / float(df["Close"].iloc[0])
fig_eq.add_trace(go.Scatter(
    x=btc_bh.index, y=btc_bh.values,
    name="⚫ BTC Buy & Hold",
    line=dict(color="#888888", width=1.5, dash="dot"),
    opacity=0.6,
    hovertemplate="%{x|%Y-%m-%d}  ×%{y:.4f}<extra></extra>",
))

# Faint orange bands marking each OOS test window
for w in windows:
    fig_eq.add_vrect(
        x0=str(w["test_start"]), x1=str(w["test_end"]),
        fillcolor="#ff7f0e", opacity=0.04, layer="below", line_width=0,
    )

fig_eq.update_layout(
    title=(
        f"{strategy_name}  ·  Portfolio Equity (Base = 1.0)  ·  "
        f"Fees & Slippage Deducted  ·  Butterworth cutoff = {butterworth_cutoff}  ·  "
        "Orange bands = OOS test windows"
    ),
    xaxis_title="Date",
    yaxis_title="Portfolio Value (× initial capital)",
    height=640,
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    template="plotly_white",
    margin=dict(l=70, r=20, t=100, b=60),
)
st.plotly_chart(fig_eq, use_container_width=True)

st.divider()


# ═════════════════════════════════════════════════════════════════════════════
# SECTION D — 🤖 .env PARAMETER OPTIMIZATION AGENT
# ═════════════════════════════════════════════════════════════════════════════
st.subheader("🤖 .env Parameter Optimization Agent")
st.caption(
    "Coordinate-descent sweep around current .env values.  "
    "Varies one parameter at a time (×0.7, ×0.85, ×1.0, ×1.15, ×1.30), "
    "evaluates full walk-forward OOS Sharpe, and proposes changes.  "
    f"Reads and writes: `{ENV_PATH}`"
)

# Display current .env params
env_display = read_env_display(strategy_name)
with st.expander("📋 Current .env Parameters", expanded=True):
    if env_display:
        env_df = pd.DataFrame(
            [{"ENV Variable": k, "Current Value": v} for k, v in env_display.items()]
        )
        st.dataframe(env_df, use_container_width=True, hide_index=True)
    else:
        st.info("No .env parameters mapped for this strategy.")

sweep_col1, sweep_col2 = st.columns([1, 3])
with sweep_col1:
    sweep_btn = st.button("▶ Run Agent Sweep", type="secondary", use_container_width=True)
    st.caption("≈ 30–90 seconds depending on strategy and date range")

if sweep_btn:
    current_params = read_env_params(strategy_name)

    with st.spinner("Agent running coordinate-descent parameter sweep…"):
        best_params, best_sharpe, trial_log = run_parameter_sweep(
            df, butterworth_cutoff, strategy_name, current_params, windows,
        )

    proposal = build_proposal(strategy_name, current_params, best_params)

    # ── Results ───────────────────────────────────────────────────────────────
    st.subheader("🔬 Agent Results")

    # Baseline OOS Sharpe with current params
    from agent import _eval_config
    baseline_sharpe = _eval_config(df, butterworth_cutoff, strategy_name,
                                   current_params, windows)

    sharpe_delta = best_sharpe - baseline_sharpe
    st.metric(
        "OOS Sharpe Improvement",
        value=f"{best_sharpe:.3f}",
        delta=f"{sharpe_delta:+.3f} vs current .env",
    )

    with st.expander("📊 Full Parameter Trial Log", expanded=False):
        st.dataframe(pd.DataFrame(trial_log), use_container_width=True, hide_index=True)

    # ── Proposed changes ──────────────────────────────────────────────────────
    if proposal:
        st.success(
            f"Agent found {len(proposal)} parameter improvement(s).  "
            "Review proposed changes below before applying."
        )
        prop_df = pd.DataFrame(proposal)
        st.dataframe(prop_df, use_container_width=True, hide_index=True)

        st.warning(
            "⚠️ Applying these changes will **overwrite** the live trading bot's `.env` file.  "
            "The bot must be **restarted** for changes to take effect."
        )

        if st.button("✅ Apply Changes to .env", type="primary"):
            try:
                updated = apply_to_env(proposal)
                st.success(
                    f"Successfully updated {len(updated)} variable(s): "
                    + ", ".join(updated)
                )
                st.info("Restart the trading bot (`python -m trading_bot_v2.api_server`) to activate new parameters.")
            except Exception as e:
                st.error(f"Failed to write .env: {e}")
    else:
        st.info(
            "Current .env parameters are already at or near the optimal values "
            "for this date range and strategy.  No changes proposed."
        )


# ─────────────────────────────────────────────────────────────────────────────
# APPENDIX — Fold Table
# ─────────────────────────────────────────────────────────────────────────────
if fold_results:
    with st.expander("🔍 Fold-by-Fold Breakdown"):
        st.dataframe(pd.DataFrame(fold_results), use_container_width=True, hide_index=True)

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────────────────────────────────────
st.caption(
    f"**Strategy:** {strategy_name}  ·  "
    f"**Butterworth cutoff:** {butterworth_cutoff} (causal lfilter, no lookahead bias)  ·  "
    "**Costs:** 0.10% fee + 0.05% slippage per side  ·  "
    "**Data:** yfinance daily adjusted close  ·  "
    "For educational purposes only — not financial advice."
)
