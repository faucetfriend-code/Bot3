"""
Conditional forward-return and excursion study for anchored-VWAP setups.

WHAT THIS IS FOR
----------------
Every VWAP measurement in this project so far has run a full strategy and
read one number (profit factor) off the end. That conflates the signal with
the stop, the target, the costs and position occupancy, and it costs minutes
to hours per configuration. This measures the *signal* instead, and it is
the input to the design rather than a validation of it.

Three questions, in order:

1. Is there any horizon at which an anchored-VWAP deviation predicts a
   forward move at all - and in WHICH direction? The project has always
   assumed reversion. That assumption has never been tested against
   continuation, on an asset that trends 62% of bars.
2. What does the joint MFE/MAE (maximum favourable / adverse excursion)
   distribution look like? Given it, every stop/target pair can be
   evaluated analytically, with no further backtests.
3. Does conditioning on time-of-day or relative volume separate a
   population that reverts from one that continues?

DESIGN SLICE DISCIPLINE
-----------------------
Defaults to BTC-USDC, 2018-01-01 .. 2024-01-01. 2024-2026 and every other
symbol are deliberately held out and must stay untouched until a finished
candidate exists. The reason is on record: the regime census's best VWAP
cell showed PF 1.05 and came back 0.78 at 7.6x the sample, and 14 of 14
out-of-sample folds of the threshold walk-forward lost money.

NO LOOKAHEAD
------------
The anchored VWAP at bar i uses only bars from the session open through i.
Entry is the close of bar i. The forward window starts at bar i+1. Setups
whose forward window would run past the end of the slice are dropped.

OVERLAPPING SAMPLES
-------------------
Consecutive setups share forward bars, so a naive t-statistic over all
setups is badly overstated. Every reported t-statistic uses a greedily
de-overlapped subsample (setups spaced at least one horizon apart); the
full-sample mean is reported alongside it for reference.

Usage:
    python scripts/analysis/vwap_signal_study.py
    python scripts/analysis/vwap_signal_study.py --anchor rolling --rolling-bars 60
    python scripts/analysis/vwap_signal_study.py --symbol ETH-USDC   # HOLDOUT - do not
"""

import argparse
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Round-trip friction measured for BTC under the `pacifica` cost profile:
# 7.5 bp exchange fees + 6.6 bp slippage. See docs/VWAP-LEVERS.md lever 3.
ROUND_TRIP_COST = 0.00141

DEFAULT_THRESHOLDS = (1.0, 1.5, 2.0, 2.5, 3.0)
# 15m bars: 1 -> 15m, 2 -> 30m, 4 -> 1h, 8 -> 2h, 16 -> 4h, 32 -> 8h, 96 -> 1d
DEFAULT_HORIZONS = (1, 2, 4, 8, 16, 32, 96)
# Minimum bars into a session before its volume-weighted sigma is usable.
MIN_SESSION_BARS = 8


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def load_candles(data_dir: str, symbol: str, timeframe: str,
                 start: str, end: str) -> pd.DataFrame:
    """
    Load one timeframe of the canonical parquet store.

    Args:
        data_dir: Directory holding ``{symbol}_{timeframe}.parquet``.
        symbol: e.g. "BTC-USDC".
        timeframe: e.g. "15m".
        start: Inclusive ISO date.
        end: Exclusive ISO date.

    Returns:
        DataFrame indexed by UTC timestamp with OHLCV columns.

    Raises:
        FileNotFoundError: If the parquet is missing.
    """
    path = os.path.join(data_dir, f"{symbol}_{timeframe}.parquet")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Candle parquets are gitignored; see "
            f"docs/BACKTESTING_GUIDE.md 'Candle data is not in git'."
        )
    df = pd.read_parquet(path)
    ts_col = "timestamp" if "timestamp" in df.columns else df.columns[0]
    # The store writes ISO strings, not epoch millis.
    df[ts_col] = pd.to_datetime(df[ts_col])
    df = df.set_index(ts_col).sort_index()
    df = df[~df.index.duplicated(keep="first")]

    # Explicit half-open interval. `.loc["2018-01-01":"2024-01-01"]` would use
    # pandas partial-string indexing and include ALL of 2024-01-01, leaking a
    # day of holdout past the design slice.
    mask = df.index >= pd.Timestamp(start)
    if end:
        mask &= df.index < pd.Timestamp(end)
    return df.loc[mask]


# ---------------------------------------------------------------------------
# The indicator
# ---------------------------------------------------------------------------


def anchored_vwap(df: pd.DataFrame, anchor: str = "session",
                  rolling_bars: int = 60) -> pd.DataFrame:
    """
    Volume-weighted average price and volume-weighted sigma.

    Two anchor modes, because the shipped strategy uses the second one and
    the difference is a design question rather than a tuning one:

    - ``session``: cumulative from 00:00 UTC each day, reset daily. This is
      what "VWAP" means - a session execution benchmark. Its value depends
      only on the tape.
    - ``rolling``: cumulative over a trailing fixed window, which is what
      ``vwap_scalping.py`` computes. Its value depends on how many candles
      the caller passes (``BACKTEST_HISTORY_LOOKBACK``), i.e. on a backtest
      configuration knob, so it is not well defined across environments.

    Args:
        df: OHLCV frame indexed by timestamp.
        anchor: "session" or "rolling".
        rolling_bars: Window length when anchor="rolling".

    Returns:
        DataFrame with "vwap", "sigma" and "deviation_sd" columns. Rows
        without a usable sigma carry NaN.

    Raises:
        ValueError: If anchor is unknown.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    # Keep zero-volume bars as zeros rather than NaN: a NaN would blank the
    # bar's cumulative sums and punch holes through the session.
    vol = df["volume"].astype(float)
    pv = typical * vol
    p2v = typical * typical * vol

    if anchor == "session":
        day = df.index.normalize()
        grp_pv = pv.groupby(day).cumsum()
        grp_v = vol.groupby(day).cumsum()
        grp_p2v = p2v.groupby(day).cumsum()
        bars_in = pd.Series(1, index=df.index).groupby(day).cumsum()
        usable = bars_in >= MIN_SESSION_BARS
    elif anchor == "rolling":
        grp_pv = pv.rolling(rolling_bars, min_periods=rolling_bars).sum()
        grp_v = vol.rolling(rolling_bars, min_periods=rolling_bars).sum()
        grp_p2v = p2v.rolling(rolling_bars, min_periods=rolling_bars).sum()
        usable = grp_v.notna()
    else:
        raise ValueError(f"anchor must be 'session' or 'rolling', got {anchor!r}")

    vwap = grp_pv / grp_v
    # Volume-weighted variance: E[p^2] - E[p]^2, clipped for float error.
    variance = (grp_p2v / grp_v) - vwap * vwap
    sigma = np.sqrt(variance.clip(lower=0.0))

    out = pd.DataFrame(index=df.index)
    out["vwap"] = vwap.where(usable)
    out["sigma"] = sigma.where(usable & (sigma > 0))
    out["deviation_sd"] = (df["close"] - out["vwap"]) / out["sigma"]
    return out


def relative_volume(df: pd.DataFrame, lookback: int = 96) -> pd.Series:
    """
    Volume divided by its trailing median, as a participation proxy.

    Uses a strictly trailing window (shifted by one bar) so the current
    bar's own volume cannot enter its own baseline.

    Args:
        df: OHLCV frame.
        lookback: Bars in the trailing median (96 x 15m = 1 day).

    Returns:
        Series of volume ratios.
    """
    base = df["volume"].shift(1).rolling(lookback, min_periods=lookback // 2).median()
    return df["volume"] / base


# ---------------------------------------------------------------------------
# Forward windows
# ---------------------------------------------------------------------------


def forward_matrices(
    df: pd.DataFrame, setup_idx: np.ndarray, horizon: int
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Bars 1..horizon after each setup, as (n_setups, horizon) matrices.

    Args:
        df: OHLCV frame.
        setup_idx: Positional indices of setup bars.
        horizon: Number of forward bars.

    Returns:
        (highs, lows, closes), each (n_setups, horizon).
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    offsets = np.arange(1, horizon + 1)[None, :]
    idx = setup_idx[:, None] + offsets
    return high[idx], low[idx], close[idx]


def signed_excursions(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    entry: np.ndarray, direction: np.ndarray,
) -> Dict[str, np.ndarray]:
    """
    Express a forward window in signed return space.

    ``direction`` is +1 for a long and -1 for a short, so all four series
    below are positive-is-good regardless of side. This is what lets longs
    and shorts be pooled without a sign bug.

    Args:
        highs: (n, h) forward highs.
        lows: (n, h) forward lows.
        closes: (n, h) forward closes.
        entry: (n,) entry prices.
        direction: (n,) +1 long / -1 short.

    Returns:
        Dict with:
          "best": (n, h) best favourable move reached by each bar
          "worst": (n, h) worst adverse move reached by each bar
          "terminal": (n,) signed return at the end of the horizon
          "mfe": (n,) maximum favourable excursion over the window
          "mae": (n,) maximum adverse excursion (<= 0)
    """
    e = entry[:, None]
    d = direction[:, None]

    # A long's favourable extreme is the high; a short's is the low.
    fav_price = np.where(d > 0, highs, lows)
    adv_price = np.where(d > 0, lows, highs)

    best = d * (fav_price / e - 1.0)
    worst = d * (adv_price / e - 1.0)

    return {
        "best": best,
        "worst": worst,
        "terminal": (direction * (closes[:, -1] / entry - 1.0)),
        "mfe": np.maximum.accumulate(best, axis=1)[:, -1],
        "mae": np.minimum.accumulate(worst, axis=1)[:, -1],
    }


def de_overlapped(setup_idx: np.ndarray, horizon: int) -> np.ndarray:
    """
    Greedily pick setups at least ``horizon`` bars apart.

    Overlapping forward windows share bars, so their returns are strongly
    autocorrelated and a t-statistic over all of them is overstated. This
    is the cheap, transparent alternative to a Newey-West correction.

    Args:
        setup_idx: Sorted positional indices.
        horizon: Minimum spacing in bars.

    Returns:
        Boolean mask over ``setup_idx``.
    """
    keep = np.zeros(len(setup_idx), dtype=bool)
    last = -(10 ** 9)
    for i, pos in enumerate(setup_idx):
        if pos - last >= horizon:
            keep[i] = True
            last = pos
    return keep


def t_stat(x: np.ndarray) -> float:
    """Student t against a zero mean; 0.0 for degenerate samples."""
    if len(x) < 3:
        return 0.0
    sd = float(np.std(x, ddof=1))
    if sd == 0.0:
        return 0.0
    return float(np.mean(x) / (sd / np.sqrt(len(x))))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

BP = 10_000.0


def report_unconditional(df: pd.DataFrame, horizons: Tuple[int, ...]) -> None:
    """
    Baseline drift, so a conditional 'edge' cannot just be BTC going up.

    A long-biased signal on an asset that appreciated over the slice will
    look profitable for reasons that have nothing to do with the signal.
    Every conditional number later must be read against this row.
    """
    print("\n" + "=" * 78)
    print("UNCONDITIONAL FORWARD RETURN (baseline drift, long direction)")
    print("=" * 78)
    print(f"  {'horizon':>9}{'mean bp':>12}{'median bp':>12}{'n':>10}")
    print("  " + "-" * 43)
    close = df["close"].to_numpy(dtype=float)
    for h in horizons:
        if h >= len(close):
            continue
        r = close[h:] / close[:-h] - 1.0
        print(
            f"  {_hlabel(h):>9}{np.mean(r) * BP:>12.2f}"
            f"{np.median(r) * BP:>12.2f}{len(r):>10}"
        )


def _hlabel(h: int) -> str:
    """Human label for a horizon in 15m bars."""
    minutes = h * 15
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 1440:
        return f"{minutes // 60}h"
    return f"{minutes // 1440}d"


def report_conditional(
    df: pd.DataFrame, ind: pd.DataFrame,
    thresholds: Tuple[float, ...], horizons: Tuple[int, ...],
) -> pd.DataFrame:
    """
    Forward return in the REVERSION direction, per threshold and horizon.

    Positive means fading the deviation made money before costs; negative
    means the deviation continued and the strategy should be a
    continuation strategy instead.

    Returns:
        Tidy DataFrame of every (threshold, horizon, side) cell.
    """
    print("\n" + "=" * 78)
    print("CONDITIONAL FORWARD RETURN, REVERSION DIRECTION, GROSS OF COSTS")
    print(f"(positive = fading works; round-trip cost is {ROUND_TRIP_COST * BP:.1f} bp)")
    print("=" * 78)

    close = df["close"].to_numpy(dtype=float)
    dev = ind["deviation_sd"].to_numpy(dtype=float)
    n = len(df)
    rows: List[dict] = []
    # One setup universe for every horizon, so the rows are comparable: a
    # short horizon must not get extra setups near the end of the slice.
    max_h = max(horizons)

    for thr in thresholds:
        print(f"\n  deviation_sd >= {thr}")
        print(
            f"  {'horizon':>8}{'side':>7}{'n':>8}{'mean bp':>10}"
            f"{'med bp':>9}{'t(deov)':>9}{'n_deov':>8}{'win%':>7}"
        )
        print("  " + "-" * 66)
        for h in horizons:
            for side_name, mask_side in (
                ("long", dev <= -thr),   # price below VWAP -> fade = buy
                ("short", dev >= thr),   # price above VWAP -> fade = sell
                ("both", np.abs(dev) >= thr),
            ):
                valid = mask_side & np.isfinite(dev)
                valid[n - max_h - 1:] = False
                idx = np.flatnonzero(valid)
                if len(idx) < 30:
                    continue

                direction = np.where(dev[idx] < 0, 1.0, -1.0)
                highs, lows, closes = forward_matrices(df, idx, h)
                exc = signed_excursions(
                    highs, lows, closes, close[idx], direction
                )
                term = exc["terminal"]

                keep = de_overlapped(idx, h)
                rows.append({
                    "threshold": thr, "horizon": h, "side": side_name,
                    "n": len(idx), "mean_bp": float(np.mean(term) * BP),
                    "median_bp": float(np.median(term) * BP),
                    "t_deoverlapped": t_stat(term[keep]),
                    "n_deoverlapped": int(keep.sum()),
                    "win_pct": float((term > 0).mean() * 100),
                    "mfe_bp": float(np.mean(exc["mfe"]) * BP),
                    "mae_bp": float(np.mean(exc["mae"]) * BP),
                })
                r = rows[-1]
                print(
                    f"  {_hlabel(h):>8}{side_name:>7}{r['n']:>8}"
                    f"{r['mean_bp']:>10.2f}{r['median_bp']:>9.2f}"
                    f"{r['t_deoverlapped']:>9.2f}{r['n_deoverlapped']:>8}"
                    f"{r['win_pct']:>7.1f}"
                )
    return pd.DataFrame(rows)


def report_excursions(tidy: pd.DataFrame, threshold: float) -> None:
    """
    Mean MFE against mean MAE - the geometry budget.

    If MFE is not comfortably larger than the cost, no stop/target pair
    can rescue the setup no matter how it is placed.
    """
    print("\n" + "=" * 78)
    print(f"EXCURSION BUDGET at deviation_sd >= {threshold} (reversion direction)")
    print("=" * 78)
    sub = tidy[(tidy["threshold"] == threshold) & (tidy["side"] == "both")]
    print(f"  {'horizon':>9}{'MFE bp':>10}{'MAE bp':>10}{'MFE/|MAE|':>12}{'cost bp':>10}")
    print("  " + "-" * 51)
    for _, r in sub.iterrows():
        ratio = r["mfe_bp"] / abs(r["mae_bp"]) if r["mae_bp"] else float("nan")
        print(
            f"  {_hlabel(int(r['horizon'])):>9}{r['mfe_bp']:>10.1f}"
            f"{r['mae_bp']:>10.1f}{ratio:>12.2f}{ROUND_TRIP_COST * BP:>10.1f}"
        )


def report_by_year(
    df: pd.DataFrame, ind: pd.DataFrame, threshold: float, horizon: int,
    side: str = "long",
) -> None:
    """
    Per-calendar-year forward return, against that year's own drift.

    THE control for the most likely false positive here. A "buy the dip"
    signal looks profitable in any appreciating sample, and BTC appreciated
    enormously across the design slice. If the long-side edge is real it
    should survive in the bear years (2018, 2022); if it is drift wearing a
    costume, it will be concentrated in 2019-2021 and 2023.

    Args:
        df: OHLCV frame.
        ind: Indicator frame from anchored_vwap.
        threshold: Minimum absolute deviation_sd.
        horizon: Forward bars.
        side: "long" (below VWAP) or "short" (above VWAP).
    """
    close = df["close"].to_numpy(dtype=float)
    dev = ind["deviation_sd"].to_numpy(dtype=float)
    n = len(df)

    mask = (dev <= -threshold) if side == "long" else (dev >= threshold)
    mask &= np.isfinite(dev)
    mask[n - horizon - 1:] = False
    idx = np.flatnonzero(mask)
    if len(idx) < 60:
        return

    direction = np.full(len(idx), 1.0 if side == "long" else -1.0)
    highs, lows, closes = forward_matrices(df, idx, horizon)
    term = signed_excursions(highs, lows, closes, close[idx], direction)["terminal"]

    print("\n" + "=" * 78)
    print(
        f"BY YEAR: {side} reversion, deviation_sd >= {threshold}, "
        f"horizon {_hlabel(horizon)} (gross)"
    )
    print("  'drift' is the same-year unconditional move over the same horizon,")
    print("  signed for this side. 'excess' is the part the signal adds.")
    print("=" * 78)
    print(
        f"  {'year':>6}{'n':>7}{'mean bp':>10}{'drift bp':>10}"
        f"{'excess bp':>11}{'t(deov)':>9}{'net bp':>9}"
    )
    print("  " + "-" * 62)

    years = df.index[idx].year.to_numpy()
    all_years = df.index.year.to_numpy()
    sign = 1.0 if side == "long" else -1.0

    for yr in sorted(set(years.tolist())):
        m = years == yr
        if m.sum() < 30:
            continue
        ysel = all_years == yr
        yclose = close[ysel]
        drift = (
            sign * np.mean(yclose[horizon:] / yclose[:-horizon] - 1.0)
            if len(yclose) > horizon else float("nan")
        )
        keep = de_overlapped(idx[m], horizon)
        mean_bp = float(np.mean(term[m]) * BP)
        print(
            f"  {yr:>6}{int(m.sum()):>7}{mean_bp:>10.2f}{drift * BP:>10.2f}"
            f"{mean_bp - drift * BP:>11.2f}{t_stat(term[m][keep]):>9.2f}"
            f"{mean_bp - ROUND_TRIP_COST * BP:>9.2f}"
        )


def report_first_touch(
    df: pd.DataFrame, ind: pd.DataFrame, threshold: float, horizon: int,
    stops: Tuple[float, ...], targets: Tuple[float, ...],
    side: str = "both",
) -> None:
    """
    Evaluate a stop/target grid analytically from first-touch order.

    Distances are in units of the entry bar's volume-weighted sigma, which
    is the strategy's own scale. Both levels are checked bar by bar; when
    a single bar touches both, the stop is assumed to fill first, matching
    the pessimistic convention and avoiding a flattering ambiguity.

    Expected value is net of one round trip of costs.
    """
    print("\n" + "=" * 78)
    print(
        f"STOP/TARGET GRID  ({side}, deviation_sd >= {threshold}, "
        f"horizon {_hlabel(horizon)}, net of {ROUND_TRIP_COST * BP:.1f} bp)"
    )
    print("  distances in entry-bar sigma; same-bar ties resolved as STOP")
    print("=" * 78)

    close = df["close"].to_numpy(dtype=float)
    dev = ind["deviation_sd"].to_numpy(dtype=float)
    sigma = ind["sigma"].to_numpy(dtype=float)
    n = len(df)

    # Sides must be separated: the conditional table shows longs and shorts
    # have opposite signs, so a pooled grid averages an edge against a loss
    # and reports neither.
    if side == "long":
        valid = dev <= -threshold
    elif side == "short":
        valid = dev >= threshold
    else:
        valid = np.abs(dev) >= threshold
    valid &= np.isfinite(dev) & np.isfinite(sigma)
    valid[n - horizon - 1:] = False
    idx = np.flatnonzero(valid)
    if len(idx) < 30:
        print("  too few setups")
        return

    direction = np.where(dev[idx] < 0, 1.0, -1.0)
    entry = close[idx]
    highs, lows, closes = forward_matrices(df, idx, horizon)
    exc = signed_excursions(highs, lows, closes, entry, direction)
    # Sigma expressed as a fractional move, so grid levels are returns.
    sig_frac = sigma[idx] / entry

    print(f"  setups: {len(idx)}   median sigma: {np.median(sig_frac) * 100:.3f}% of price")
    header = "  stop\\tgt " + "".join(f"{t:>10.1f}" for t in targets)
    print(header)
    print("  " + "-" * (len(header) - 2))

    for s in stops:
        cells = []
        for t in targets:
            tgt_level = (t * sig_frac)[:, None]
            stop_level = -(s * sig_frac)[:, None]

            hit_tgt = exc["best"] >= tgt_level
            hit_stop = exc["worst"] <= stop_level
            big = horizon + 1
            first_tgt = np.where(hit_tgt.any(1), hit_tgt.argmax(1), big)
            first_stop = np.where(hit_stop.any(1), hit_stop.argmax(1), big)

            won = first_tgt < first_stop
            lost = first_stop <= first_tgt
            timed_out = (first_tgt == big) & (first_stop == big)
            won &= ~timed_out
            lost &= ~timed_out

            pnl = np.where(
                won, t * sig_frac,
                np.where(lost, -s * sig_frac, exc["terminal"]),
            )
            cells.append(float(np.mean(pnl) - ROUND_TRIP_COST) * BP)
        print(f"  {s:>8.1f} " + "".join(f"{c:>10.2f}" for c in cells))
    print("\n  (values are mean bp per trade, net; > 0 means a tradeable cell)")


def report_conditioning(
    df: pd.DataFrame, ind: pd.DataFrame, threshold: float, horizon: int
) -> None:
    """
    Split the setup population by hour-of-day and by relative volume.

    Both are hypotheses about *when* a deviation reverts rather than *how
    far* it has gone. The always-on shipped strategy cannot express either.
    """
    close = df["close"].to_numpy(dtype=float)
    dev = ind["deviation_sd"].to_numpy(dtype=float)
    n = len(df)
    valid = (np.abs(dev) >= threshold) & np.isfinite(dev)
    valid[n - horizon - 1:] = False
    idx = np.flatnonzero(valid)
    if len(idx) < 60:
        return

    direction = np.where(dev[idx] < 0, 1.0, -1.0)
    highs, lows, closes = forward_matrices(df, idx, horizon)
    term = signed_excursions(highs, lows, closes, close[idx], direction)["terminal"]

    print("\n" + "=" * 78)
    print(
        f"CONDITIONING at deviation_sd >= {threshold}, horizon {_hlabel(horizon)} "
        f"(reversion, gross)"
    )
    print("=" * 78)

    hours = df.index[idx].hour.to_numpy()
    print("\n  by UTC hour")
    print(f"  {'hour':>6}{'n':>8}{'mean bp':>10}{'t':>8}")
    print("  " + "-" * 32)
    for hr in range(0, 24, 2):
        m = (hours >= hr) & (hours < hr + 2)
        if m.sum() < 30:
            continue
        sel = idx[m]
        keep = de_overlapped(sel, horizon)
        print(
            f"  {hr:>4}-{hr + 2:<2}{int(m.sum()):>6}{np.mean(term[m]) * BP:>10.2f}"
            f"{t_stat(term[m][keep]):>8.2f}"
        )

    rvol = relative_volume(df).to_numpy(dtype=float)[idx]
    print("\n  by relative volume (vs trailing 1d median)")
    print(f"  {'bucket':>14}{'n':>8}{'mean bp':>10}{'t':>8}")
    print("  " + "-" * 40)
    edges = [(0, 0.75), (0.75, 1.0), (1.0, 1.5), (1.5, 3.0), (3.0, np.inf)]
    for lo, hi in edges:
        m = np.isfinite(rvol) & (rvol >= lo) & (rvol < hi)
        if m.sum() < 30:
            continue
        sel = idx[m]
        keep = de_overlapped(sel, horizon)
        label = f"{lo:g}-{hi:g}" if np.isfinite(hi) else f"{lo:g}+"
        print(
            f"  {label:>14}{int(m.sum()):>8}{np.mean(term[m]) * BP:>10.2f}"
            f"{t_stat(term[m][keep]):>8.2f}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    """Run the study and print every table. Returns a process exit code."""
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--symbol", default="BTC-USDC")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--start", default="2018-01-01", help="design slice start")
    p.add_argument("--end", default="2024-01-01", help="design slice end (exclusive)")
    p.add_argument(
        "--data-dir",
        default=os.path.join(REPO_ROOT, "trading_bot_v2", "backtesting", "data"),
    )
    p.add_argument("--anchor", default="session", choices=("session", "rolling"))
    p.add_argument("--rolling-bars", type=int, default=60)
    p.add_argument("--focus-threshold", type=float, default=2.0)
    p.add_argument("--focus-horizon", type=int, default=16)
    p.add_argument(
        "--rvol-max", type=float, default=None,
        help=(
            "Keep only setups whose bar volume is below this multiple of its "
            "trailing 1d median. EXPLORATORY: the volume split was chosen "
            "after seeing the conditioning table, so any result here is "
            "in-sample and needs the holdout to mean anything."
        ),
    )
    p.add_argument("--csv", default=None, help="write the tidy table here")
    args = p.parse_args(argv)

    if args.symbol != "BTC-USDC" or args.end > "2024-01-01":
        print(
            "!! WARNING: the design slice is BTC-USDC 2018-01-01..2024-01-01.\n"
            "!! Everything outside it is holdout reserved for a finished\n"
            "!! candidate. Proceeding, but this run is no longer clean.\n"
        )

    df = load_candles(
        args.data_dir, args.symbol, args.timeframe, args.start, args.end
    )
    print("=" * 78)
    print("VWAP SIGNAL STUDY")
    print("=" * 78)
    print(f"  symbol     : {args.symbol} {args.timeframe}")
    print(f"  slice      : {df.index[0]} .. {df.index[-1]}  ({len(df)} bars)")
    print(f"  anchor     : {args.anchor}"
          + (f" ({args.rolling_bars} bars)" if args.anchor == "rolling" else ""))
    print(f"  cost model : {ROUND_TRIP_COST * BP:.1f} bp per round trip")

    ind = anchored_vwap(df, anchor=args.anchor, rolling_bars=args.rolling_bars)

    if args.rvol_max is not None:
        rvol = relative_volume(df)
        before = int(ind["deviation_sd"].notna().sum())
        # Blank the deviation so the filter propagates to every report.
        ind.loc[~(rvol < args.rvol_max), "deviation_sd"] = np.nan
        after = int(ind["deviation_sd"].notna().sum())
        print(f"  rvol filter: < {args.rvol_max}  ({after}/{before} bars kept)")
        print("  EXPLORATORY - split chosen after seeing the data; needs holdout")

    dev = ind["deviation_sd"].dropna()
    print(f"\n  deviation_sd: mean {dev.abs().mean():.2f}  "
          f"p95 {dev.abs().quantile(.95):.2f}  "
          f"p99 {dev.abs().quantile(.99):.2f}  max {dev.abs().max():.2f}")
    print(f"  bars past 2.0 SD: {(dev.abs() >= 2.0).mean() * 100:.1f}%")

    report_unconditional(df, DEFAULT_HORIZONS)
    tidy = report_conditional(df, ind, DEFAULT_THRESHOLDS, DEFAULT_HORIZONS)
    report_excursions(tidy, args.focus_threshold)
    for side in ("long", "short"):
        report_first_touch(
            df, ind, args.focus_threshold, args.focus_horizon,
            stops=(1.0, 2.0, 3.0, 5.0, 8.0),
            targets=(0.25, 0.5, 1.0, 1.5, 2.0),
            side=side,
        )
    report_by_year(df, ind, args.focus_threshold, args.focus_horizon, side="long")
    report_conditioning(df, ind, args.focus_threshold, args.focus_horizon)

    if args.csv:
        tidy.to_csv(args.csv, index=False)
        print(f"\n  tidy table -> {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
