"""Tests for the monthly re-tune driver's pure logic (no subprocesses)."""

import json

from trading_bot_v2.optimization.monthly_retune import (
    ADOPTED_PARAMS,
    _fresh_medians,
    _month_window,
    _render_markdown,
    _shift_months,
    compare_retune,
)


class TestMonthWindow:
    def test_explicit_month(self):
        assert _month_window("2026-06") == (
            "2026-06-01", "2026-07-01", "2026-06")

    def test_december_rolls_year(self):
        assert _month_window("2025-12") == (
            "2025-12-01", "2026-01-01", "2025-12")

    def test_default_is_a_valid_window(self):
        start, end, label = _month_window(None)
        assert start < end
        assert label == start[:7]


class TestShiftMonths:
    def test_back_36(self):
        assert _shift_months("2026-08-01", -36) == "2023-08-01"

    def test_back_across_year(self):
        assert _shift_months("2026-01-01", -2) == "2025-11-01"


class TestCompare:
    def _report(self, tmp_path, params):
        report = {
            "summary": {
                "vol_low:trend": {
                    "folds": 2, "tuned": 1.2, "default": 0.7, "edge": 0.5}
            },
            "folds": [
                {"states": {"vol_low:trend": {"params": params}}},
                {"states": {"vol_low:trend": {"params": params}}},
            ],
        }
        path = tmp_path / "r.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def test_fresh_medians(self):
        report = {"folds": [
            {"states": {"s": {"params": {"a": 1.0}}}},
            {"states": {"s": {"params": {"a": 3.0}}}},
        ]}
        assert _fresh_medians(report) == {"s": {"a": 2.0}}

    def test_drift_vs_adopted(self, tmp_path):
        adopted = ADOPTED_PARAMS["mean_reversion"]["vol_low:trend"]
        path = self._report(tmp_path, dict(adopted))
        cmp = compare_retune(path)
        entry = cmp["vol_low:trend"]
        assert entry["adopted_params"] == adopted
        assert all(v == 0.0
                   for v in entry["median_drift_vs_adopted"].values())

    def test_missing_report(self, tmp_path):
        assert "error" in compare_retune(tmp_path / "nope.json")


class TestMarkdown:
    def test_renders_scorecard_and_comparison(self):
        payload = {
            "scorecard": [
                {"strategy": "vwap_pullback", "symbol": "BTC-USDC",
                 "closed_trades": 61, "profit_factor": 2.374,
                 "net_pnl": 19.54, "return_pct": 0.2,
                 "win_rate_pct": 60.0, "max_drawdown_pct": 1.0},
                {"strategy": "grid_trading", "symbol": "SUI-USDC",
                 "error": "ValueError: no data"},
            ],
            "comparison": {
                "vol_low:trend": {
                    "oos": {"folds": 4, "tuned": 1.1, "default": 0.7,
                            "edge": 0.4},
                    "adopted_params": {"a": 1.0},
                },
            },
        }
        md = _render_markdown("2026-07", payload)
        assert "| vwap_pullback | BTC-USDC | 61 | 2.374 |" in md
        assert "ERROR: ValueError: no data" in md
        assert "| vol_low:trend | 4 | 1.1 | 0.7 | 0.4 | yes |" in md
        assert "Adoption rule" in md
