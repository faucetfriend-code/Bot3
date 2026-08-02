# Drive migration runbook

Moving the working tree off `C:` (e.g. to `D:\Bot3` or `G:\Bot3`).

Audited 2026-08-02 against `retune-measurement-hardening` @ f4c967f. The tree is
**4.3 GB** on disk; roughly 2.5 GB of that is regenerable or disposable, so a
naive `xcopy` moves several gigabytes of cache you would rather rebuild.

The good news first: the design is mostly portable already. Both launchers derive
their root from `%~dp0`, `.env` uses only relative paths, and the package resolves
its own location from `__file__`. The breakages below are a short, specific list.

Throughout, `<NEW>` means the new absolute root, e.g. `D:\Bot3`.

---

## 0. What actually breaks

Fix these; everything else in the tree survives the move untouched.

| # | Item | Class | Fix |
|---|------|-------|-----|
| 1 | `trading_bot_v2/monitor_ai.bat`, `monitor_alerts.bat` | BREAKS | Replace the hardcoded `cd` with `cd /d "%~dp0"` (see 4.1) |
| 2 | `__editable__.trading_bot-2.1.0.pth` in site-packages | degrades silently | Delete the stale editable install (see 4.2) |
| 3 | `.claude/settings.local.json` permission allowlist | degrades silently | Re-approve, or search/replace the old root (see 4.3) |
| 4 | `monthly-retune` scheduled task prompt | BREAKS the task | Edit its `SKILL.md` working directory (see 4.4) |
| 5 | `DEFAULT_LOG_DIR` in `monthly_retune.py` | cosmetic | Machine-specific, not move-specific. Optional env override (see 4.5) |
| 6 | `BTV2/tests/test_regime_aware_vwap.py:21` | BREAKS that one script | Archive-only. Fix or ignore (see 4.6) |
| 7 | `DATABASE_PATH` / `BACKTEST_DATA_DIR` resolve against the **working directory** | degrades silently | Nothing to edit. Always launch from the repo root (see 4.7) |

---

## 1. Stop everything first

Nothing here tolerates being copied mid-write. The SQLite database and the
parquet store are the two that will bite you.

1. **Stop the API server.** It is running now (port 8000). Use the dashboard's
   stop control, or close the `run_bot.bat` window. Confirm the port is free:

   ```
   netstat -ano | findstr :8000
   ```

   Expect no `LISTENING` row. If one remains, `taskkill /F /PID <pid>`.
   Copying `trading_bot.db` while the server holds it open risks a torn file or
   an orphaned `-wal`/`-shm` pair.

2. **Check the monthly re-tune is not about to fire.** It is a Claude Code
   scheduled task, *not* a Windows Task Scheduler entry, so it will not show up
   in `taskschd.msc`:

   - id: `monthly-retune`
   - schedule: `0 7 1 * *` (07:00, day 1 of the month)
   - definition: `C:\Users\z_shi\.claude\scheduled-tasks\monthly-retune\SKILL.md`
   - last run 2026-08-01, next run 2026-09-01

   If you are migrating near the 1st, disable it before you start and re-enable
   after step 5 passes. A run that begins mid-move will fail on a half-copied
   candle store and write a misleading scorecard.

3. **Let any background validation or backtest runs finish.** They write into
   `out/` and into the log directory, and they hold parquet files open.

4. **Quiesce the other agents.** Two agents were working concurrently in
   `strategies/mean_reversion.py`, `optimization/monthly_retune.py` and
   `core_logic/` at audit time. Migrating under them loses work.

---

## 2. What to move, what to leave

### Must move (expensive or impossible to recreate)

| Path | Size | Why |
|------|------|-----|
| `trading_bot_v2/backtesting/data/` | **575 MB**, 18 parquet + CSV | Multi-year candle and funding history, BTC back to 2018 and funding to 2019-09-10. Re-downloading is hours of Binance API calls and is rate-limited. **This is the single most important thing to move.** It is gitignored, so a fresh clone does not bring it. |
| `.env` | 18 KB | Credentials and 100+ tuned parameters. Gitignored, exists nowhere else. Copy it by hand; do not regenerate. |
| `trading_bot.db` | 1.8 MB | Live trade and signal history. Kelly sizing activates at 50+ trades, so losing it resets the sizer. |
| `signals_log.csv` | 630 KB | Gitignored signal log. |
| `out/` | 296 KB | Untracked. Contains the monthly re-tune reports and scorecards, which are deliverables. |
| `config/regime_param_overlays.json` | 77 B | Small but it is live tuned state. |
| `backups/` | 17.9 MB | Rolling DB backups. Move if you want the history; safe to drop if you do not. |
| `.git/` | 160 MB | Obviously. Move the tree, do not re-clone, or you lose every gitignored artefact above. |

### Do not move - regenerate or discard

| Path | Size | Why |
|------|------|-----|
| `__pycache__/`, `*.pyc` | ~8 MB | Rebuilt on first import. Stale `.pyc` actively causes "behaviour does not match code" confusion (see CLAUDE.md). 145 untracked ones were cleared during this audit. |
| `.mypy_cache/` | 82 MB | Rebuilt by mypy. Contains absolute paths. |
| `.pytest_cache/`, `.ruff_cache/` | 283 KB | Rebuilt on next run. |
| `.claude/worktrees/` | **1.5 GB** | Disposable agent worktrees - six full copies of the tree. Delete before moving; this is the largest single win. |
| `node_modules/` | 13.9 MB | `npm install` rebuilds it. (It is also wrongly committed - see section 6.) |
| `bot_console.err.log` | **279 MB** | Log flood. |
| `validation_run_mean_reversion.log` | **230 MB** | Log flood. |
| `validation_run_ma_crossover_eth.log` | **49 MB** | Log flood. |
| `server logs reports/` | **654 MB** | Historical log dump. Archive to `G:` if you want it; do not carry it to the new root. |
| `_junk/` | **801 MB** | Named for what it is. |
| `trading_bot.egg-info/` | 411 B | Stale build metadata from a `setup.py` that packages `core_logic`. Regenerated on demand. |
| `bandit-report.json`, `safety-report.json` | 1.4 MB | Security scan output from 2026-06-29, full of absolute paths to the old root. Regenerate if you want current findings. |

Dropping the "do not move" column takes the copy from 4.3 GB to roughly 1.8 GB,
and most of what remains is `.git/` and the candle store.

### Suggested copy

Robocopy preserves timestamps and handles the long paths under `node_modules`
and `.git` better than Explorer drag-and-drop:

```
robocopy "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot3" "<NEW>" /E /COPY:DAT /R:2 /W:2 ^
  /XD __pycache__ .mypy_cache .pytest_cache .ruff_cache node_modules _junk ^
      "server logs reports" worktrees ^
  /XF *.pyc *.pyo bot_console.err.log validation_run_*.log
```

Copy first, verify (section 5), and only then delete the original. Do not use
`/MOVE` on the first pass.

---

## 3. Things that need no attention

Confirmed portable by inspection - listed so you do not go hunting:

- `run_bot.bat` and `run_validation.bat` set `PYTHONPATH` from `%~dp0`, so they
  follow the tree wherever it goes.
- `.env` contains **no** absolute paths. `DATABASE_PATH=trading_bot.db` and
  `BACKTEST_DATA_DIR=trading_bot_v2/backtesting/data` are both relative and
  survive the move (with the caveat in 4.7). Every other path-shaped key is a
  remote URL.
- `pyproject.toml`, `setup.py`, `pyrightconfig.json`, `.vscode/settings.json`
  use relative paths or `${workspaceFolder}`.
- `docker-compose.yml` bind mounts are all relative (`./data:/app/data`).
- `.claude/launch.json` does not exist, so there is no dev-server config to fix.
- No Windows Task Scheduler entry references this tree (all 21 non-Microsoft
  tasks were checked; the ones matching are for the unrelated "Agent OS" project).
- The dead `sys.path.insert` calls are gone from the live package.
  `trading_bot_v2/imports.py` is a tombstone recording their removal. The one in
  `api_server.py:16` still points at a non-existent `../Example files/core_logic`,
  but it is `__file__`-relative, so it is harmless dead code rather than a
  migration hazard.

---

## 4. Post-move edits

### 4.1 The two monitor batch files (BREAKS)

`trading_bot_v2/monitor_ai.bat` and `trading_bot_v2/monitor_alerts.bat` both do:

```
cd "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2"
```

Note the space: `Bot 3`, not `Bot3`. That directory exists as a separate sibling
project, so these scripts have been silently running against **the wrong tree**,
not failing loudly. Fix both to be self-locating:

```
cd /d "%~dp0"
```

That is the same trick `run_bot.bat` already uses, and it makes them immune to
any future move.

### 4.2 The stale editable install (degrades silently)

```
C:\Users\z_shi\AppData\Roaming\Python\Python314\site-packages\__editable__.trading_bot-2.1.0.pth
```

contains a single line: `C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3` - again the
sibling with the space. This injects that other tree onto `sys.path` for *every*
Python process on the machine, so `import core_logic` can resolve to the wrong
project. Running `python -m` from the repo root currently masks it because the
working directory is searched first.

Delete the stale install:

```
pip uninstall trading-bot
```

If `pip` cannot find it, delete the `.pth` file and the
`trading_bot-2.1.0.dist-info/` directory beside it by hand. Only re-run
`pip install -e .` from `<NEW>` if you actually need `core_logic` importable
from outside the repo - nothing in `trading_bot_v2/` imports it today.

### 4.3 Claude Code permission allowlist (degrades silently)

`.claude/settings.local.json` holds 31 permission entries keyed on the absolute
path `C:\\Users\\z_shi\\Desktop\\N8NPROJECTS\\Bot3`. After the move they stop
matching and you get permission prompts for commands that used to be
pre-approved. Nothing breaks; it is friction.

Either re-approve on demand, or search/replace the old root with `<NEW>` in that
file. It is a per-machine file and should arguably not be tracked at all
(section 6).

### 4.4 The monthly re-tune task (BREAKS the task)

`C:\Users\z_shi\.claude\scheduled-tasks\monthly-retune\SKILL.md` states on line 8:

```
Working directory: C:\Users\z_shi\Desktop\N8NPROJECTS\Bot3
```

and line 24 points at the project memory directory
`C:\Users\z_shi\.claude\projects\C--Users-z-shi-Desktop-N8NPROJECTS-Bot3\memory\`.

Update the working directory to `<NEW>`. The memory path is keyed on the project
directory name, so Claude Code will start a new memory namespace for the new
location - if you want the accumulated project memory to follow, copy the old
`memory/` directory into the new namespace once it exists.

Do this **before** the next 1st of the month.

### 4.5 The re-tune log directory (cosmetic)

`trading_bot_v2/optimization/monthly_retune.py:96`:

```python
DEFAULT_LOG_DIR = r"G:\Candle Data\Temp Test holding"
```

This is a deliberate off-`C:` choice - run logs have twice filled `C:` - and it
already degrades gracefully: `_resolve_log_dir()` falls back to
`out/monthly/logs` if the directory cannot be created. It is machine-specific
rather than move-specific, so **the move does not break it**. If `G:` is present
on the target machine, nothing to do.

To make it overridable without editing code, the additive change is:

```python
DEFAULT_LOG_DIR = os.environ.get(
    "RETUNE_LOG_DIR", r"G:\Candle Data\Temp Test holding"
)
```

The default is unchanged, so existing behaviour and the recorded provenance in
`out/monthly/retune-*.json` stay identical. This was **not applied** during the
audit because another agent held that file (see section 7).

### 4.6 One hardcoded path in the BTV2 archive (BREAKS that script)

`BTV2/tests/test_regime_aware_vwap.py:21`:

```python
sys.path.insert(0, "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot3/BTV2")
```

The only absolute path of its kind in the tree. Replace with the idiom used by
the other 248 call sites:

```python
sys.path.insert(0, str(Path(__file__).parent.parent))
```

`BTV2/` is a read-only archive and is not on the default pytest path, so this
does not affect the suite. Fix it or leave it, but know it is there.

### 4.7 Working-directory sensitivity (degrades silently)

`trading_bot_v2/database.py:61` resolves the database at import time:

```python
DATABASE_PATH = os.path.abspath(os.getenv("DATABASE_PATH", "trading_bot.db"))
```

`os.path.abspath` is relative to the **current working directory**, not to the
package. `BACKTEST_DATA_DIR` behaves the same way. The move does not break this,
but it means:

> Launching from anywhere other than the repo root silently creates a brand new
> empty `trading_bot.db` in that directory, and reports an empty candle store.

There is no error - you get a bot with no history and a backtest with no data.
Always launch via `run_bot.bat` (which is self-locating) or `cd <NEW>` first.
If you want belt and braces, set absolute paths in `.env` after the move.

---

## 5. Verify the move

Run in order from `<NEW>`. Stop at the first failure.

**1. The tree is where you think it is**

```
cd /d <NEW>
git status
git log --oneline -1
```

Expect the same branch and commit you left, and no unexpected deletions.
Large gitignored files (`.env`, the parquet store) will not appear in
`git status` - check them explicitly:

```
dir .env trading_bot.db
dir trading_bot_v2\backtesting\data\*.parquet
```

Expect `.env` at ~18 KB and **18** parquet files.

**2. The package imports and finds its own config**

```
python -c "import trading_bot_v2.config as c; print(c.config.database_path, c.config.backtest_data_dir)"
```

Expect the two relative values from `.env`, and no traceback.

**3. The candle store is intact and readable**

```
python -m trading_bot_v2.data_manager --symbols BTC-USDC,ETH-USDC,SUI-USDC --timeframes 5m,15m,1h,4h --coverage
```

Expect coverage rows with BTC reaching back to 2018 and a trailing edge matching
what you had pre-move. A row that reports zero candles means the store did not
copy, or you are running from the wrong directory (see 4.7).

Then the funding series, which is the part that cannot be casually re-downloaded:

```
python -m trading_bot_v2.data_manager --symbols BTC-USDC --coverage --funding
```

Expect BTC funding from 2019-09-10.

**4. The database survived**

```
python -c "import sqlite3;c=sqlite3.connect('trading_bot.db');print(c.execute('select count(*) from trades').fetchone())"
```

Expect the same trade count as before the move. Note it down beforehand.

**5. The suite**

```
python -m pytest trading_bot_v2/ -q
```

Expect **2022 passed, 1 deselected**, zero failures. This is the gate: it
exercises config loading, the data layer, path resolution and the strategies.

**6. The server starts and serves**

Double-click `run_bot.bat`, then open http://localhost:8000 and confirm the
dashboard renders with the balance and regime populated. Leave the bot stopped
until you are satisfied - starting the server is not the same as starting
trading.

**7. Re-enable the scheduled task** once 1-6 pass, having applied 4.4.

Only after all seven: delete the original tree from `C:`.

---

## 6. Repository hygiene noticed during the audit

Not migration blockers, but they make the move heavier than it needs to be.
Listed as recommendations - none of these were changed.

**Tracked, but arguably should not be:**

- `node_modules/` - **639 files committed** despite being in `.gitignore`. The
  ignore rule was added after the commit, so it has no effect.
  Remedy: `git rm -r --cached node_modules`.
- `trading_bot.db` - a live, mutable SQLite database in version control. Every
  trade dirties the tree. Remedy: `git rm --cached trading_bot.db`.
- 52 `.pyc` files under `trading_bot_v2/` and 39 more under `BTV2/` are committed
  bytecode. These are exactly the stale-cache hazard CLAUDE.md warns about.
  Remedy: `git rm --cached` them, then let `.gitignore` do its job.
- `.claude/settings.local.json` - per-machine, path-keyed (see 4.3).
- `trading_bot.egg-info/` - generated build metadata.

**Untracked and unignored** (so it shows in every `git status`):

- `out/` - 20 files. The monthly reports under `out/monthly/` are genuine
  deliverables and the one-off study JSONs at `out/` top level are not, so this
  wants a decision rather than a blanket rule. Two sane options: ignore `out/`
  and commit `out/monthly/` explicitly via a negation, or commit `out/monthly/`
  and ignore the rest.
- `docs/bot upgrade plan.md` - last touched 2026-06-30, referenced by nothing.
  A stale planning doc; delete or fold into the current docs.

---

## 7. Note on the audit itself

`monthly_retune.py` was excluded from editing because another agent held it
during this audit, so the env override in 4.5 is written up but not applied.
The `.gitignore` additions and the `__pycache__` cleanup described above were
applied.
