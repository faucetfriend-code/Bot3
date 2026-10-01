"""
Supervisor Control — pause/resume gate for new trade entries.

Lets a supervisor (Claude routine, human, monitoring system) pause new entries
without killing the bot. Existing positions continue to be managed normally —
only NEW signals are blocked while paused.

State is persisted at SUPERVISOR_STATE_PATH when configured, otherwise in
`supervisor_pause.json` at the project root, so it survives bot restarts.

Wire-up
-------
- API endpoints in `api_server.py` call `pause()` / `resume()` / `status()`
- `trading_bot._should_execute_signal()` calls `is_paused()` first

Pause semantics
---------------
- Pause has an optional `until_ts` (ISO-8601 UTC). If set and reached, the
  pause auto-expires on the next `is_paused()` call. Otherwise pause is
  indefinite until `resume()` is called.
- `reason` is free-form text for audit (logged with each blocked signal).
- Paused signals are logged via `signal_logger.log_signal_rejected()` with
  reason="supervisor pause: <reason>".

Safety properties
-----------------
- A missing state file defaults to unpaused for compatibility.
- Corrupt or unreadable state fails closed: new entries remain paused until
  an operator repairs the state or explicitly resumes.
- File write is atomic (write to .tmp, rename) to avoid partial reads.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Pause file lives at project root (Bot3/supervisor_pause.json)
# alongside trading_bot.db and other runtime state.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PAUSE_FILE = (
    Path(
        os.getenv("SUPERVISOR_STATE_PATH", "").strip()
        or str(_PROJECT_ROOT / "supervisor_pause.json")
    )
    .expanduser()
    .resolve()
)


class SupervisorControl:
    """Singleton pause-flag manager. Thread-safe."""

    _instance: Optional["SupervisorControl"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "SupervisorControl":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._file_lock = threading.Lock()
        # Don't create the file on init — fail-open if it doesn't exist
        if not _PAUSE_FILE.exists():
            logger.info(
                f"SupervisorControl initialized (no pause file at {_PAUSE_FILE})"
            )
        else:
            logger.info(f"SupervisorControl initialized (pause file at {_PAUSE_FILE})")

    # ────────────────────────────────────────────────────────────────────
    # Internal: read/write
    # ────────────────────────────────────────────────────────────────────

    def _read_state(self) -> dict:
        """Read state, preserving missing-file defaults and failing closed on damage."""
        try:
            with _PAUSE_FILE.open("r", encoding="utf-8") as f:
                state = json.load(f)
            if not isinstance(state, dict) or not isinstance(state.get("paused"), bool):
                raise ValueError("Pause state must contain a boolean paused field")
            if state.get("until_ts") is not None and not isinstance(
                state["until_ts"], str
            ):
                raise ValueError("Pause expiry must be a string or null")
            return state
        except FileNotFoundError:
            return {}
        except (ValueError, OSError) as e:
            logger.error(
                f"SupervisorControl: cannot trust pause state ({e}); "
                "new entries paused until operator recovery"
            )
            return {
                "paused": True,
                "reason": "Pause state unreadable; operator recovery required",
            }

    def _write_state(self, state: dict) -> None:
        """Atomic write: tmp file then rename."""
        tmp = _PAUSE_FILE.with_suffix(".json.tmp")
        with self._file_lock:
            _PAUSE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(_PAUSE_FILE)

    # ────────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────────

    def is_paused(self) -> bool:
        """
        True if new entries should be blocked. Auto-expires `until_ts` if past.

        Side effect: if the pause has expired, writes the file to clear it.
        """
        state = self._read_state()
        if not state.get("paused"):
            return False

        until_ts = state.get("until_ts")
        if until_ts:
            try:
                until_dt = datetime.fromisoformat(until_ts.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) >= until_dt:
                    # Expired — auto-resume
                    logger.info(
                        f"SupervisorControl: pause expired at {until_ts}, auto-resuming"
                    )
                    self._write_state(
                        {
                            "paused": False,
                            "expired_at": until_ts,
                            "expired_reason": state.get("reason"),
                        }
                    )
                    return False
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"SupervisorControl: bad until_ts format {until_ts!r} ({e}); "
                    f"treating pause as indefinite"
                )

        return True

    def pause(
        self, reason: str = "no reason given", until_ts: Optional[str] = None
    ) -> dict:
        """
        Pause new entries. Existing positions are unaffected.

        Parameters
        ----------
        reason : free-form audit text
        until_ts : optional ISO-8601 UTC timestamp for auto-expiry.
                   None = indefinite (must be cleared by resume()).

        Returns the new state dict.
        """
        state = {
            "paused": True,
            "reason": reason,
            "since_ts": datetime.now(timezone.utc).isoformat(),
            "until_ts": until_ts,
        }
        self._write_state(state)
        logger.warning(
            f"SupervisorControl: PAUSED ({reason}) "
            f"{'until ' + until_ts if until_ts else 'indefinitely'}"
        )
        return state

    def resume(self) -> dict:
        """Clear the pause. Returns new state."""
        state = {
            "paused": False,
            "resumed_ts": datetime.now(timezone.utc).isoformat(),
        }
        self._write_state(state)
        logger.info("SupervisorControl: RESUMED — new entries allowed")
        return state

    def status(self) -> dict:
        """Return full state with computed `is_paused` boolean."""
        state = self._read_state()
        return {
            "is_paused": self.is_paused(),  # this auto-expires if past until_ts
            "raw_state": state,
            "pause_file": str(_PAUSE_FILE),
        }


# Convenience singleton accessor
def get_supervisor_control() -> SupervisorControl:
    return SupervisorControl()
