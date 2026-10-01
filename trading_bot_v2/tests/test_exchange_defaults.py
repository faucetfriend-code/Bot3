"""Offline checks for consistent Pacifica defaults and explicit Blofin selection."""

from typing import Optional
from unittest.mock import MagicMock

import pytest

from trading_bot_v2.config_validation import check_live_credentials
from trading_bot_v2.exchanges import get_exchange_capabilities, get_exchange_client
from trading_bot_v2.history.trade_store import get_active_exchange_name
from trading_bot_v2.ws_factory import active_exchange


@pytest.mark.parametrize("selection", [None, "", "   ", " PACIFICA ", "blofin"])
def test_exchange_selection_stays_consistent(
    monkeypatch: pytest.MonkeyPatch, selection: Optional[str]
) -> None:
    """Orders, market data and history resolve the same configured venue."""
    if selection is None:
        monkeypatch.delenv("EXCHANGE", raising=False)
    else:
        monkeypatch.setenv("EXCHANGE", selection)
    expected = "blofin" if selection == "blofin" else "pacifica"
    adapter = get_exchange_client(rest_client=MagicMock(), ws_client=MagicMock())
    assert adapter.capabilities().name == expected
    assert get_exchange_capabilities().name == expected
    assert active_exchange() == expected
    assert get_active_exchange_name() == expected


def test_explicit_blofin_override_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pacifica configuration does not remove the explicit Blofin option."""
    monkeypatch.setenv("EXCHANGE", "pacifica")
    adapter = get_exchange_client(
        "blofin", rest_client=MagicMock(), ws_client=MagicMock()
    )
    assert adapter.capabilities().name == "blofin"


@pytest.mark.parametrize("selection", [None, "", "   ", "pacifica"])
def test_default_validation_respects_testnet(selection: Optional[str]) -> None:
    """Selecting Pacifica does not change safe mode or bypass live key checks."""
    assert not check_live_credentials({"EXCHANGE": selection, "TESTNET": "true"})
    errors = check_live_credentials({"EXCHANGE": selection, "TESTNET": "false"})
    assert errors and "EXCHANGE=pacifica" in errors[0]
