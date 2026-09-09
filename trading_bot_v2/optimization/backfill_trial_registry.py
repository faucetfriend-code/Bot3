"""Register the composite campaign's search, which was never recorded.

``run_composite_tuning`` drives Optuna directly instead of going through
``OptunaRunner``, and until 2026-08-02 nothing on that path wrote to the
``trial_registry``. Every composite run from 2026-07-28 on is therefore
missing from the table that supplies the Deflated Sharpe Ratio's N
(``validation/gate.py:649``, ``validation/runner.py:990``), so every DSR
computed against the campaign deflated too little - an under-penalty,
opposite in sign to the 740 test-artifact rows purged the same day.

The counts ARE recoverable: each report records ``trials_per_fold`` and
its list of folds, and the product is the study size. This script walks
those artifacts and registers one row per fold, matching the live
recorder's granularity.

What is NOT recoverable is ``sr_variance``: it is the variance of the
per-TRIAL objective values within a study, and the artifacts keep only
per-fold, per-state results. Those rows are written with NULL variance,
which ``get_trial_sr_variance`` already skips. Do not synthesise it from
fold scores - that is a different quantity and would silently corrupt
the DSR benchmark.

DE-DUPLICATION IS BY PROVENANCE, NOT BY CONTENT, and the direction of
the remaining error is deliberate. Identical output does NOT mean one
search: ``composite_grid_pf_gateoff.json`` and
``composite_grid_pf_gateenforce.json`` are byte-identical (GridTrading is
in ``DEFAULT_GATE_EXEMPT``, so the two directional-gate arms cannot
differ) yet they are two separate runs that each burned 125 trials.
Collapsing them because their results coincide would under-count N, and
under-counting deflates too little - the same anti-conservative error
this whole backfill exists to correct. Over-counting merely penalises
harder, so when in doubt this script counts.

The one true duplicate is structural, not statistical: the monthly
driver writes ``out/monthly/_tune/composite_*.json`` and then COPIES it
to ``out/monthly/``. That directory is excluded by provenance
(``--include-intermediate`` overrides). Identity is then the artifact's
repo-relative path plus a content hash, so re-running is a no-op.

When two counted artifacts do share a content hash the script says so
loudly and counts both, leaving the judgement with the operator.

sr_variance stays NULL for every backfilled row.

Usage:
    python -m trading_bot_v2.optimization.backfill_trial_registry --dry-run
    python -m trading_bot_v2.optimization.backfill_trial_registry
"""

import argparse
import glob
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Optional

#: Prefix marking a row as reconstructed from an artifact rather than
#: written by the run itself. Greppable, and it keeps backfilled rows
#: distinguishable from live ones forever.
SOURCE_PREFIX = "backfill:composite"

#: Where composite reports are written.
DEFAULT_GLOB = "out/**/*.json"


def is_composite_report(payload: Any) -> bool:
    """Is this JSON a composite walk-forward report?

    Args:
        payload: Parsed JSON.

    Returns:
        True when it carries the keys the backfill needs.
    """
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("folds"), list)
        and bool(payload.get("folds"))
        and isinstance(payload.get("trials_per_fold"), int)
        and isinstance(payload.get("strategy"), str)
    )


def content_hash(payload: Dict[str, Any]) -> str:
    """Stable short hash of a report's fold RESULTS.

    Two files holding the same run hash the same however they are named
    or wherever they sit; two runs that differ in any fold outcome hash
    differently. That is what makes re-running this script a no-op and
    what keeps the gate-off / gate-enforce arms apart.

    Args:
        payload: Parsed composite report.

    Returns:
        First 12 hex chars of the SHA-256 over the canonicalised folds.
    """
    canonical = json.dumps(payload["folds"], sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def normalize_path(path: str) -> str:
    """Repo-relative path with forward slashes, for use in an id.

    Args:
        path: Path as globbed.

    Returns:
        A stable string identifying the artifact across platforms.
    """
    return os.path.normpath(path).replace(os.sep, "/")


def fold_sources(payload: Dict[str, Any], path: str) -> List[str]:
    """Source strings for every fold in one report.

    The id carries BOTH the artifact path and a hash of its fold
    results: the path is what makes two genuinely separate runs count
    separately even when their output coincides, and the hash is what
    makes a re-run against edited artifacts visible rather than silent.

    Args:
        payload: Parsed composite report.
        path: Path the report was read from.

    Returns:
        One source string per fold, in report order.
    """
    digest = content_hash(payload)
    strategy = payload["strategy"]
    symbol = payload.get("symbol", "unknown")
    where = normalize_path(path)
    out = []
    for index, fold in enumerate(payload["folds"], 1):
        train = fold.get("train") or ["?", "?"]
        test = fold.get("test") or ["?", "?"]
        out.append(
            f"{SOURCE_PREFIX}/{strategy}/{symbol}/{where}#{digest}/"
            f"f{index}/{train[0]}_{train[1]}/{test[0]}_{test[1]}"
        )
    return out


def _existing_sources(db) -> set:
    """Every source string already in the registry."""
    return {row.get("source") for row in db.get_trial_registry() if row.get("source")}


def is_intermediate(path: str) -> bool:
    """Is this a driver scratch copy rather than a distinct run?

    ``monthly_retune`` writes its tuning report into ``_tune/`` and then
    copies it to the canonical ``out/monthly/`` path, so counting both
    would register one search twice.

    Args:
        path: Artifact path.

    Returns:
        True when the path sits under a ``_tune`` directory.
    """
    return "_tune" in normalize_path(path).split("/")


def plan(paths: List[str], include_intermediate: bool = False) -> List[Dict[str, Any]]:
    """Build the row plan from artifacts.

    Args:
        paths: Candidate JSON paths.
        include_intermediate: Count ``_tune/`` scratch copies too.

    Returns:
        One entry per row to insert, each with ``source``, ``strategy``,
        ``n_trials`` and the originating ``path``.
    """
    rows: List[Dict[str, Any]] = []
    digests: Dict[str, str] = {}
    for path in sorted(paths):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if not is_composite_report(payload):
            continue
        if is_intermediate(path) and not include_intermediate:
            print(f"  skip (driver scratch copy): {normalize_path(path)}")
            continue
        digest = content_hash(payload)
        if digest in digests:
            # Counted anyway - see the module docstring on why identical
            # output does not imply a single search.
            print(
                f"  NOTE: identical results to {digests[digest]}: "
                f"{normalize_path(path)} (counted as a separate search)"
            )
        else:
            digests[digest] = normalize_path(path)
        for source in fold_sources(payload, path):
            rows.append(
                {
                    "source": source,
                    "strategy": payload["strategy"],
                    "n_trials": payload["trials_per_fold"],
                    "path": path,
                }
            )
    return rows


def run(argv: Optional[List[str]] = None) -> int:
    """Backfill the registry from composite artifacts.

    Args:
        argv: Argument vector, or None for ``sys.argv[1:]``.

    Returns:
        0 on success.
    """
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--glob", default=DEFAULT_GLOB)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be inserted, insert nothing",
    )
    p.add_argument(
        "--include-intermediate",
        action="store_true",
        help=(
            "Also count reports under _tune/. Those are the monthly "
            "driver's scratch copies of a report it also writes to "
            "out/monthly/, so counting them registers one search twice."
        ),
    )
    args = p.parse_args(argv)

    from ..database import DatabaseManager

    db = DatabaseManager()
    already = _existing_sources(db)
    rows = plan(glob.glob(args.glob, recursive=True), args.include_intermediate)
    fresh = [r for r in rows if r["source"] not in already]

    by_strategy: Dict[str, List[int]] = {}
    for row in fresh:
        by_strategy.setdefault(row["strategy"], []).append(row["n_trials"])

    print(f"\ncandidate rows : {len(rows)}")
    print(f"already present: {len(rows) - len(fresh)}")
    print(f"to insert      : {len(fresh)}")
    for strategy, trials in sorted(by_strategy.items()):
        before = db.get_total_trials(strategy)
        print(
            f"  {strategy:<18} +{len(trials):>3} rows, "
            f"+{sum(trials):>5} trials (N {before} -> "
            f"{before + sum(trials)})"
        )

    if args.dry_run:
        print("\ndry run - nothing written")
        return 0

    for row in fresh:
        db.save_trial_registry_entry(
            strategy=row["strategy"],
            regime=None,
            scope="optuna_study",
            n_trials=row["n_trials"],
            sr_variance=None,  # not recoverable; see module docstring
            source=row["source"],
        )
    print(f"\ninserted {len(fresh)} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(run())
