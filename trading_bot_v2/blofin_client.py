"""Blofin REST client: raw implementation against docs.blofin.com.

SDK-vs-raw decision
-------------------
The official SDK (``pip install blofin``, v0.5.0) was inspected and
rejected: its base URL is a hard-coded module constant with NO demo
trading support (the demo host is a different origin, not a flag), it
has no WebSocket client, no retries, and its request layer cannot be
pointed at ``https://demo-trading-openapi.blofin.com``.  Because
``BLOFIN_DEMO=true`` is this bot's default mode, the SDK is unusable
as-is.  Its ``auth.py`` was, however, used to confirm the signature
scheme, which this module reimplements (see ``BlofinClient.sign``).

Signature scheme (verified against SDK source + docs.blofin.com):
    prehash   = request_path + METHOD + timestamp_ms + nonce + body
    signature = base64(hex(hmac_sha256(secret, prehash)).encode())
Headers: ACCESS-KEY, ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-NONCE,
ACCESS-PASSPHRASE.  For GET requests the query string is part of
``request_path`` and the body is empty; for POST the body is the exact
JSON string sent on the wire.

Response shape contract ("bot-native" surface)
----------------------------------------------
The legacy call sites in this codebase (grid lifecycle, api_server,
funding_arb, risk manager) parse Pacifica-shaped dicts: bare base
symbols ("BTC"), lowercase "long"/"short" position sides, "buy"/"sell"
order sides, base-currency amounts, and keys like ``order_id``,
``entry_price``, ``funding_rate``.  This client therefore converts
Blofin wire vocabulary (``instId`` "BTC-USDT", sizes in CONTRACTS with
per-instrument ``contractValue``/``lotSize``) to those bot-native
shapes at its own boundary, so ``EXCHANGE=blofin`` is a drop-in swap.

Verified against live public endpoints on 2026-07-20:
    * /market/instruments: 506 SWAP instruments; bases are DUPLICATED
      across USDT/USDC/USD quotes -> the symbol map filters to
      quoteCurrency == "USDT" only.
    * /market/candles: rows [ts,o,h,l,c,vol,volCurrency,volCurrencyQuote,
      confirm], NEWEST FIRST; vol is contracts, volCurrency base units.
    * /market/funding-rate-history: timestamps 8h apart (3x/day).
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

BLOFIN_REST_URL = "https://openapi.blofin.com"
BLOFIN_DEMO_REST_URL = "https://demo-trading-openapi.blofin.com"
API_TIMEOUT = 15

# Bot interval -> Blofin bar parameter (case matters: hours are uppercase).
BAR_MAP = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "6h": "6H",
    "8h": "8H",
    "12h": "12H",
    "1d": "1D",
    "3d": "3D",
    "1w": "1W",
}

_MISSING_KEYS_MSG = (
    "Blofin API credentials are not configured. Add BLOFIN_API_KEY, "
    "BLOFIN_API_SECRET and BLOFIN_PASSPHRASE to .env (create the keys "
    "at blofin.com -> API Management; start with DEMO trading keys and "
    "BLOFIN_DEMO=true), then restart the bot."
)


class BlofinAuthError(Exception):
    """Raised when an auth-required call is made without credentials."""


class BlofinAPIError(Exception):
    """Raised when the Blofin API returns a non-zero business code."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Blofin API error {code}: {message}")


def _env_bool(name: str, default: str = "true") -> bool:
    """Read a boolean environment variable ('true'/'false')."""
    return os.getenv(name, default).strip().lower() == "true"


def _dec(value: Any) -> Decimal:
    """Convert a value to Decimal via str (avoids float noise)."""
    return Decimal(str(value))


def _fmt(value: Decimal) -> str:
    """Format a Decimal as a plain (non-scientific) trimmed string."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _strip_perp(symbol: str) -> str:
    """Normalize a bot symbol: strip -PERP/-USDT suffixes, uppercase."""
    cleaned = symbol.strip().upper()
    for suffix in ("-PERP", "-USDT"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned


class BlofinClient:
    """REST client for Blofin USDT-margined perpetual futures.

    Public market-data methods work without credentials.  Private
    (account/trading) methods raise :class:`BlofinAuthError` with setup
    instructions when credentials are missing.

    Args:
        api_key: Blofin API key (default: ``BLOFIN_API_KEY`` env var).
        api_secret: API secret (default: ``BLOFIN_API_SECRET``).
        passphrase: API passphrase (default: ``BLOFIN_PASSPHRASE``).
        demo: Use the demo-trading host (default: ``BLOFIN_DEMO`` env
            var, which itself defaults to true).
        margin_mode: "cross" or "isolated" for new orders (default:
            ``BLOFIN_MARGIN_MODE`` env var, defaulting to "cross").
        base_url: Explicit base URL override (testing).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        passphrase: Optional[str] = None,
        demo: Optional[bool] = None,
        margin_mode: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("BLOFIN_API_KEY", "")
        self.api_secret = (
            api_secret if api_secret is not None else os.getenv("BLOFIN_API_SECRET", "")
        )
        self.passphrase = (
            passphrase if passphrase is not None else os.getenv("BLOFIN_PASSPHRASE", "")
        )
        self.demo = demo if demo is not None else _env_bool("BLOFIN_DEMO", "true")
        self.margin_mode = (
            margin_mode
            if margin_mode is not None
            else os.getenv("BLOFIN_MARGIN_MODE", "cross").strip().lower()
        )
        if base_url is not None:
            self.base_url = base_url.rstrip("/")
        else:
            self.base_url = BLOFIN_DEMO_REST_URL if self.demo else BLOFIN_REST_URL

        self.session = requests.Session()
        self._instrument_lock = threading.Lock()
        self._instruments_by_base: Dict[str, Dict[str, Any]] = {}
        self._instruments_by_inst_id: Dict[str, Dict[str, Any]] = {}
        self._position_mode_checked = False

    # ------------------------------------------------------------------
    # Auth / transport
    # ------------------------------------------------------------------

    @property
    def has_credentials(self) -> bool:
        """True when key, secret and passphrase are all present."""
        return bool(self.api_key and self.api_secret and self.passphrase)

    def _require_auth(self) -> None:
        """Raise BlofinAuthError with setup guidance if keys missing."""
        if not self.has_credentials:
            raise BlofinAuthError(_MISSING_KEYS_MSG)

    @staticmethod
    def sign(
        secret: str,
        method: str,
        request_path: str,
        timestamp: str,
        nonce: str,
        body: str = "",
    ) -> str:
        """Compute a Blofin request signature.

        prehash = request_path + METHOD + timestamp + nonce + body;
        the HMAC-SHA256 hexdigest string is then base64-encoded.

        Args:
            secret: API secret key.
            method: HTTP method ("GET"/"POST").
            request_path: Path including query string (e.g.
                "/api/v1/account/positions?instId=BTC-USDT").
            timestamp: Millisecond timestamp string.
            nonce: Unique request nonce (UUID hex).
            body: Exact JSON body string sent on the wire ("" for GET).

        Returns:
            Base64 signature string for the ACCESS-SIGN header.
        """
        prehash = f"{request_path}{method.upper()}{timestamp}{nonce}{body}"
        digest = hmac.new(
            secret.encode(), prehash.encode(), hashlib.sha256
        ).hexdigest()
        return base64.b64encode(digest.encode()).decode()

    def _auth_headers(
        self, method: str, request_path: str, body: str = ""
    ) -> Dict[str, str]:
        """Build signed request headers for a private endpoint."""
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": self.sign(
                self.api_secret, method, request_path, timestamp, nonce, body
            ),
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-NONCE": nonce,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }

    def _check_business_code(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Raise BlofinAPIError when the response code is non-zero."""
        code = str(payload.get("code", ""))
        if code not in ("0", ""):
            raise BlofinAPIError(code, str(payload.get("msg", "unknown error")))
        return payload

    def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        auth: bool = False,
        retries: int = 3,
    ) -> Dict[str, Any]:
        """Perform a GET request; retries transient network errors."""
        if auth:
            self._require_auth()
        query = ""
        if params:
            from urllib.parse import urlencode

            query = "?" + urlencode(params)
        url = self.base_url + path + query
        last_error: Optional[Exception] = None
        for attempt in range(retries):
            headers = self._auth_headers("GET", path + query) if auth else {}
            try:
                response = self.session.get(url, headers=headers, timeout=API_TIMEOUT)
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ) as exc:
                last_error = exc
                logger.warning(
                    "Blofin GET %s failed (attempt %d/%d): %s",
                    path,
                    attempt + 1,
                    retries,
                    exc,
                )
                time.sleep(min(2**attempt, 5))
                continue
            if response.status_code == 429:
                last_error = BlofinAPIError("429", "rate limit exceeded")
                time.sleep(min(2 ** (attempt + 1), 10))
                continue
            if response.status_code >= 400:
                raise BlofinAPIError(
                    str(response.status_code), response.text[:500]
                )
            return self._check_business_code(response.json())
        raise last_error if last_error else BlofinAPIError("0", "request failed")

    def _post(self, path: str, body: Any) -> Dict[str, Any]:
        """Perform a signed POST request (no retries: not idempotent)."""
        self._require_auth()
        body_str = json.dumps(body)
        headers = self._auth_headers("POST", path, body_str)
        response = self.session.post(
            self.base_url + path,
            headers=headers,
            data=body_str,
            timeout=API_TIMEOUT,
        )
        if response.status_code >= 400:
            raise BlofinAPIError(str(response.status_code), response.text[:500])
        return self._check_business_code(response.json())

    # ------------------------------------------------------------------
    # Instruments: symbol mapping + contract sizing
    # ------------------------------------------------------------------

    def _load_instruments(self, force: bool = False) -> None:
        """Populate the instrument cache from /market/instruments.

        Blofin lists the same base under multiple quotes (USDT/USDC/USD);
        only ``quoteCurrency == "USDT"`` SWAP instruments are indexed
        because the bot assumes a USDT quote.
        """
        with self._instrument_lock:
            if self._instruments_by_base and not force:
                return
            payload = self._get(
                "/api/v1/market/instruments", {"instType": "SWAP"}
            )
            by_base: Dict[str, Dict[str, Any]] = {}
            by_inst: Dict[str, Dict[str, Any]] = {}
            for inst in payload.get("data", []) or []:
                if inst.get("quoteCurrency") != "USDT":
                    continue
                if inst.get("instType") not in (None, "SWAP"):
                    continue
                base = str(inst.get("baseCurrency", "")).upper()
                inst_id = str(inst.get("instId", ""))
                if not base or not inst_id:
                    continue
                by_base[base] = inst
                by_inst[inst_id] = inst
            if not by_base:
                raise BlofinAPIError(
                    "0", "instruments endpoint returned no USDT SWAP instruments"
                )
            self._instruments_by_base = by_base
            self._instruments_by_inst_id = by_inst
            logger.info("Blofin instrument cache loaded: %d USDT perps", len(by_base))

    def get_instrument(self, symbol: str) -> Dict[str, Any]:
        """Return the raw instrument record for a bot symbol.

        Args:
            symbol: Bot symbol ("BTC", "BTC-PERP", "btc").

        Returns:
            Raw Blofin instrument dict.

        Raises:
            ValueError: If the symbol has no USDT perp on Blofin.
        """
        self._load_instruments()
        base = _strip_perp(symbol)
        inst = self._instruments_by_base.get(base)
        if inst is None:
            raise ValueError(
                f"Symbol '{symbol}' has no USDT perpetual on Blofin "
                f"(known bases: {len(self._instruments_by_base)})"
            )
        return inst

    def to_inst_id(self, symbol: str) -> str:
        """Map a bot symbol ("BTC") to a Blofin instId ("BTC-USDT")."""
        return str(self.get_instrument(symbol)["instId"])

    def from_inst_id(self, inst_id: str) -> str:
        """Map a Blofin instId ("BTC-USDT") back to a bot symbol ("BTC")."""
        inst = self._instruments_by_inst_id.get(inst_id)
        if inst is not None:
            return str(inst["baseCurrency"]).upper()
        # Tolerant fallback: strip the quote suffix.
        return inst_id.split("-", 1)[0].upper()

    def _contract_specs(self, symbol: str) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        """Return (contract_value, lot_size, min_size, tick_size) Decimals."""
        inst = self.get_instrument(symbol)
        return (
            _dec(inst.get("contractValue", "1")),
            _dec(inst.get("lotSize", "1")),
            _dec(inst.get("minSize", "0")),
            _dec(inst.get("tickSize", "0")),
        )

    def base_to_contracts(self, symbol: str, quantity: float) -> str:
        """Convert a base-currency quantity to a contract-count string.

        The contract count is rounded DOWN to the instrument lot size
        (with a tiny epsilon so exact multiples are not truncated by
        float noise).

        Args:
            symbol: Bot symbol (e.g. "BTC").
            quantity: Quantity in base currency (e.g. 0.05 BTC).

        Returns:
            Contract count as a plain decimal string (wire format).

        Raises:
            ValueError: If the rounded size is below the instrument
                minimum (message includes the minimum in base units).
        """
        if quantity <= 0:
            raise ValueError(f"Order quantity must be positive, got {quantity}")
        contract_value, lot_size, min_size, _ = self._contract_specs(symbol)
        contracts = _dec(quantity) / contract_value
        epsilon = Decimal("1e-9")
        lots = ((contracts / lot_size) + epsilon).to_integral_value(
            rounding=ROUND_DOWN
        )
        rounded = lots * lot_size
        if rounded < min_size:
            min_base = min_size * contract_value
            raise ValueError(
                f"Order size {quantity} {_strip_perp(symbol)} is below the "
                f"Blofin minimum of {_fmt(min_base)} {_strip_perp(symbol)} "
                f"({_fmt(min_size)} contracts of {_fmt(contract_value)} "
                f"{_strip_perp(symbol)} each)"
            )
        return _fmt(rounded)

    def contracts_to_base(self, symbol: str, contracts: Any) -> float:
        """Convert a contract count to a base-currency quantity float."""
        contract_value, _, _, _ = self._contract_specs(symbol)
        return float(_dec(contracts) * contract_value)

    def round_price(self, symbol: str, price: float) -> str:
        """Round a price to the instrument tick size (wire string)."""
        _, _, _, tick = self._contract_specs(symbol)
        if tick <= 0:
            return _fmt(_dec(price))
        ticks = (_dec(price) / tick).to_integral_value(rounding=ROUND_HALF_UP)
        return _fmt(ticks * tick)

    # ------------------------------------------------------------------
    # Position mode (one-way / net)
    # ------------------------------------------------------------------

    def ensure_net_position_mode(self) -> bool:
        """Verify (and try to set) one-way "net_mode" on the account.

        The bot's position model is one-way; hedge mode would split
        long/short into separate positions.  Setting the mode fails on
        Blofin while positions or open orders exist - in that case the
        user must switch to One-way mode in the Blofin UI manually.

        Returns:
            True if the account is (now) in net mode, False otherwise.
        """
        try:
            payload = self._get("/api/v1/account/position-mode", auth=True)
            data = payload.get("data") or {}
            mode = str(data.get("positionMode", ""))
            if mode == "net_mode":
                return True
            self._post(
                "/api/v1/account/set-position-mode",
                {"positionMode": "net_mode"},
            )
            logger.info("Blofin position mode set to net_mode (one-way)")
            return True
        except (BlofinAPIError, BlofinAuthError) as exc:
            logger.warning(
                "Could not verify/set Blofin one-way position mode: %s. "
                "If orders are rejected, switch to One-way mode in the "
                "Blofin UI (close positions/orders first).",
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Trading (private)
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Place an order (quantity in base currency, side buy/sell).

        Mirrors ``PacificaClient.place_order``'s signature so legacy
        call sites work unchanged.  Converts the base quantity to
        contracts and rounds limit prices to the tick size.

        Args:
            symbol: Bot symbol (e.g. "BTC").
            side: "buy" or "sell" (lowercase, exact).
            quantity: Base-currency quantity (e.g. 0.05 BTC).
            order_type: "market" or "limit".
            price: Limit price (required for limit orders).

        Returns:
            Pacifica-style ack: {"success": bool, "data": {"order_id":
            ...}, "error": ..., "raw": <full response>}.
        """
        self._require_auth()
        # Fail fast on vocabulary errors before any network I/O.
        if side not in ("buy", "sell"):
            raise ValueError(f"Unsupported order side: {side!r} (use buy/sell)")
        if order_type not in ("market", "limit"):
            raise ValueError(f"Unsupported order type: {order_type}")

        if not self._position_mode_checked:
            self._position_mode_checked = True
            self.ensure_net_position_mode()

        body: Dict[str, Any] = {
            "instId": self.to_inst_id(symbol),
            "marginMode": self.margin_mode,
            "positionSide": "net",
            "side": side,
            "orderType": order_type,
            "size": self.base_to_contracts(symbol, quantity),
            "clientOrderId": uuid.uuid4().hex[:32],
        }
        if order_type == "limit":
            if price is None:
                raise ValueError("Price is required for limit orders")
            body["price"] = self.round_price(symbol, price)

        response = self._post("/api/v1/trade/order", body)
        return self._wrap_order_ack(response)

    @staticmethod
    def _wrap_order_ack(response: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a Blofin trade ack to the Pacifica-style wrapper."""
        data = response.get("data")
        first: Dict[str, Any] = {}
        if isinstance(data, list) and data:
            first = data[0] or {}
        elif isinstance(data, dict):
            first = data
        inner_code = str(first.get("code", "0"))
        success = str(response.get("code", "0")) == "0" and inner_code in ("0", "")
        result: Dict[str, Any] = {
            "success": success,
            "data": {
                "order_id": first.get("orderId"),
                "client_order_id": first.get("clientOrderId"),
            },
            "raw": response,
        }
        if not success:
            result["error"] = first.get("msg") or response.get("msg") or "order failed"
        return result

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Cancel a single order by exchange order ID."""
        self._require_auth()
        body = {
            "instId": self.to_inst_id(symbol),
            "orderId": str(order_id),
        }
        response = self._post("/api/v1/trade/cancel-order", body)
        return self._wrap_order_ack(response)

    def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Cancel all open orders (optionally for one symbol).

        Blofin has no cancel-all endpoint; open orders are listed and
        cancelled via /trade/cancel-batch-orders in chunks of 20.
        """
        self._require_auth()
        orders = self._get_pending_orders_raw(symbol)
        if not orders:
            return {"success": True, "data": {"cancelled": 0}}
        targets = [
            {"instId": o.get("instId"), "orderId": o.get("orderId")}
            for o in orders
            if o.get("orderId")
        ]
        cancelled = 0
        errors: List[str] = []
        for start in range(0, len(targets), 20):
            chunk = targets[start : start + 20]
            try:
                self._post("/api/v1/trade/cancel-batch-orders", chunk)
                cancelled += len(chunk)
            except BlofinAPIError as exc:
                errors.append(str(exc))
        result: Dict[str, Any] = {
            "success": not errors,
            "data": {"cancelled": cancelled},
        }
        if errors:
            result["error"] = "; ".join(errors)
        return result

    def _get_pending_orders_raw(
        self, symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Fetch open orders as raw Blofin dicts."""
        params: Dict[str, Any] = {"limit": "100"}
        if symbol:
            params["instId"] = self.to_inst_id(symbol)
        payload = self._get("/api/v1/trade/orders-pending", params, auth=True)
        data = payload.get("data") or []
        return data if isinstance(data, list) else []

    def get_orders(self) -> List[Dict[str, Any]]:
        """Return open orders in the bot-native shape.

        Keys: order_id, client_order_id, symbol (bare base), side
        (buy/sell), price, amount/quantity (base units), order_type.
        """
        self._require_auth()
        self._load_instruments()
        orders: List[Dict[str, Any]] = []
        for raw in self._get_pending_orders_raw():
            inst_id = str(raw.get("instId", ""))
            base = self.from_inst_id(inst_id)
            try:
                amount = self.contracts_to_base(base, raw.get("size", "0"))
            except (ValueError, ArithmeticError):
                amount = 0.0
            orders.append(
                {
                    "order_id": str(raw.get("orderId", "")),
                    "client_order_id": raw.get("clientOrderId", ""),
                    "symbol": base,
                    "side": str(raw.get("side", "")).lower(),
                    "order_type": raw.get("orderType"),
                    "price": self._safe_float(raw.get("price")),
                    "amount": amount,
                    "quantity": amount,
                    "created_at": raw.get("createTime"),
                    "raw": raw,
                }
            )
        return orders

    def get_positions(self) -> List[Dict[str, Any]]:
        """Return open positions in the bot-native shape.

        Contracts are converted to base units; net-mode sign determines
        long/short; zero-quantity entries are dropped.
        """
        self._require_auth()
        self._load_instruments()
        payload = self._get("/api/v1/account/positions", auth=True)
        data = payload.get("data") or []
        positions: List[Dict[str, Any]] = []
        for raw in data if isinstance(data, list) else []:
            inst_id = str(raw.get("instId", ""))
            base = self.from_inst_id(inst_id)
            contracts = self._safe_float(raw.get("positions"))
            if contracts == 0.0:
                continue
            position_side = str(raw.get("positionSide", "net")).lower()
            if position_side in ("long", "short"):
                side = position_side
            else:
                side = "long" if contracts > 0 else "short"
            try:
                quantity = self.contracts_to_base(base, abs(contracts))
            except (ValueError, ArithmeticError):
                quantity = abs(contracts)
            positions.append(
                {
                    "symbol": base,
                    "side": side,
                    "amount": quantity,
                    "quantity": quantity,
                    "entry_price": self._safe_float(raw.get("averagePrice")),
                    "unrealized_pnl": self._safe_float(raw.get("unrealizedPnl")),
                    "margin_mode": raw.get("marginMode"),
                    "leverage": raw.get("leverage"),
                    "contracts": contracts,
                    "raw": raw,
                }
            )
        return positions

    def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return fill history in the bot-native shape."""
        self._require_auth()
        self._load_instruments()
        payload = self._get(
            "/api/v1/trade/fills-history",
            {"limit": str(min(limit, 100))},
            auth=True,
        )
        data = payload.get("data") or []
        trades: List[Dict[str, Any]] = []
        for raw in data if isinstance(data, list) else []:
            base = self.from_inst_id(str(raw.get("instId", "")))
            try:
                size = self.contracts_to_base(base, raw.get("fillSize", "0"))
            except (ValueError, ArithmeticError):
                size = self._safe_float(raw.get("fillSize"))
            trades.append(
                {
                    "order_id": str(raw.get("orderId", "")),
                    "trade_id": str(raw.get("tradeId", "")),
                    "symbol": base,
                    "side": str(raw.get("side", "")).lower(),
                    "size": size,
                    "quantity": size,
                    "price": self._safe_float(raw.get("fillPrice")),
                    "fee": self._safe_float(raw.get("fee")),
                    "timestamp": self._safe_int(raw.get("ts")),
                    "raw": raw,
                }
            )
        return trades

    def get_balance(self) -> Dict[str, Any]:
        """Return the futures account balance in the bot-native shape.

        Keys mirror what call sites read from Pacifica: "balance" /
        "account_equity" (total equity) and "available_to_spend".
        Handles both documented response shapes (dict with details, or
        a bare currency list).
        """
        payload = self._get("/api/v1/account/balance", auth=True)
        data = payload.get("data")
        equity = 0.0
        available = 0.0
        if isinstance(data, dict):
            equity = self._safe_float(data.get("totalEquity"))
            for detail in data.get("details") or []:
                if str(detail.get("currency", "")).upper() == "USDT":
                    available = self._safe_float(
                        detail.get("available")
                        or detail.get("availableEquity")
                    )
                    if equity == 0.0:
                        equity = self._safe_float(detail.get("equity"))
                    break
        elif isinstance(data, list):
            for detail in data:
                if str(detail.get("currency", "")).upper() == "USDT":
                    equity = self._safe_float(detail.get("balance"))
                    available = self._safe_float(detail.get("available"))
                    break
        return {
            "balance": str(equity),
            "account_equity": str(equity),
            "available_to_spend": str(available),
            "available": str(available),
            "raw": data,
        }

    # ------------------------------------------------------------------
    # Market data (public)
    # ------------------------------------------------------------------

    def get_markets(self) -> List[Dict[str, Any]]:
        """Return available USDT perps in the bot-native market shape.

        "symbol" is the bare base; tick/lot/min sizes are converted to
        BASE currency units (Pacifica semantics) with the raw contract
        fields preserved alongside.
        """
        self._load_instruments()
        markets: List[Dict[str, Any]] = []
        for base, inst in sorted(self._instruments_by_base.items()):
            contract_value = _dec(inst.get("contractValue", "1"))
            markets.append(
                {
                    "symbol": base,
                    "inst_id": inst.get("instId"),
                    "tick_size": float(_dec(inst.get("tickSize", "0"))),
                    "lot_size": float(_dec(inst.get("lotSize", "0")) * contract_value),
                    "min_order_size": float(
                        _dec(inst.get("minSize", "0")) * contract_value
                    ),
                    "contract_value": float(contract_value),
                    "max_leverage": self._safe_float(inst.get("maxLeverage")),
                    "state": inst.get("state"),
                    "raw": inst,
                }
            )
        return markets

    def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
        """Return tick_size/lot_size/min_order_size in BASE units.

        Mirrors ``PacificaClient.get_instrument_info`` including the
        all-zeros graceful-degradation contract on errors.
        """
        try:
            inst = self.get_instrument(symbol)
        except (ValueError, BlofinAPIError, requests.exceptions.RequestException) as exc:
            logger.warning("get_instrument_info failed for %s: %s", symbol, exc)
            return {"tick_size": 0.0, "lot_size": 0.0, "min_order_size": 0.0}
        contract_value = _dec(inst.get("contractValue", "1"))
        return {
            "tick_size": float(_dec(inst.get("tickSize", "0"))),
            "lot_size": float(_dec(inst.get("lotSize", "0")) * contract_value),
            "min_order_size": float(_dec(inst.get("minSize", "0")) * contract_value),
            "contract_value": float(contract_value),
            "inst_id": inst.get("instId"),
            "raw": inst,
        }

    def get_market_data(self, symbol: str) -> Dict[str, Any]:
        """Return ticker + funding snapshot in the bot-native shape.

        Keys consumed by call sites: "funding_rate", "next_funding_time",
        plus price fields ("last", "mid", "bid", "ask", "mark").
        """
        inst_id = self.to_inst_id(symbol)
        payload = self._get("/api/v1/market/tickers", {"instId": inst_id})
        tickers = payload.get("data") or []
        ticker = tickers[0] if tickers else {}
        last = self._safe_float(ticker.get("last"))
        bid = self._safe_float(ticker.get("bidPrice"))
        ask = self._safe_float(ticker.get("askPrice"))
        mid = (bid + ask) / 2 if bid and ask else last
        result = {
            "symbol": _strip_perp(symbol),
            "last": last,
            "mark": last,
            "mid": mid,
            "bid": bid,
            "ask": ask,
            "funding_rate": 0.0,
            "next_funding_time": None,
            "timestamp": self._safe_int(ticker.get("ts")),
            "raw": ticker,
        }
        try:
            funding = self.get_funding_rate(symbol)
            if funding:
                result["funding_rate"] = funding["funding_rate"]
                result["next_funding_time"] = funding["next_funding_time"]
        except (BlofinAPIError, requests.exceptions.RequestException) as exc:
            logger.debug("Funding rate unavailable for %s: %s", symbol, exc)
        return result

    def get_candles(
        self,
        market: str,
        interval: str = "15m",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Return standardized OHLCV candles (ascending timestamps).

        Blofin rows are [ts, o, h, l, c, vol(contracts), volCurrency
        (base units), volCurrencyQuote, confirm] and arrive NEWEST
        FIRST; they are reversed and mapped to the standardized dicts
        the whole bot consumes ("timestamp", "open", ..., "volume" in
        base units).

        Args:
            market: Bot symbol, "-PERP" suffix tolerated.
            interval: Bot interval (1m/5m/15m/1h/4h/... case-insensitive).
            start_time: Inclusive lower bound, ms (filtered locally).
            end_time: Inclusive upper bound, ms (filtered locally).
            limit: Maximum number of candles (Blofin cap: 1440).

        Raises:
            ValueError: If the interval has no Blofin bar equivalent.
        """
        bar = BAR_MAP.get(interval.lower())
        if bar is None:
            raise ValueError(
                f"Unsupported candle interval '{interval}' for Blofin "
                f"(supported: {', '.join(sorted(BAR_MAP))})"
            )
        inst_id = self.to_inst_id(market)
        request_limit = min(max(limit, 1), 1440)
        try:
            payload = self._get(
                "/api/v1/market/candles",
                {"instId": inst_id, "bar": bar, "limit": str(request_limit)},
            )
        except (BlofinAPIError, requests.exceptions.RequestException) as exc:
            logger.error("Failed to fetch Blofin candles for %s: %s", market, exc)
            return []
        rows = payload.get("data") or []
        candles: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 7:
                continue
            timestamp = self._safe_int(row[0])
            if start_time is not None and timestamp < start_time:
                continue
            if end_time is not None and timestamp > end_time:
                continue
            close = self._safe_float(row[4])
            if close <= 0:
                continue
            candles.append(
                {
                    "timestamp": timestamp,
                    "open": self._safe_float(row[1]),
                    "high": self._safe_float(row[2]),
                    "low": self._safe_float(row[3]),
                    "close": close,
                    "volume": self._safe_float(row[6]),  # base units
                }
            )
        candles.sort(key=lambda c: c["timestamp"])
        return candles[-limit:]

    def get_funding_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return the current funding snapshot (rate per 8h interval)."""
        inst_id = self.to_inst_id(symbol)
        payload = self._get("/api/v1/market/funding-rate", {"instId": inst_id})
        data = payload.get("data") or []
        if not data:
            return None
        entry = data[0]
        return {
            "symbol": _strip_perp(symbol),
            "funding_rate": self._safe_float(entry.get("fundingRate")),
            "next_funding_time": self._safe_int(entry.get("fundingTime")),
        }

    def get_funding_history(
        self, symbol: str, limit: int = 8
    ) -> List[Dict[str, Any]]:
        """Return funding-rate history records (newest first).

        Record keys match the Pacifica shape read by FundingArb:
        "funding_rate", "timestamp", "next_funding".
        """
        inst_id = self.to_inst_id(symbol)
        try:
            payload = self._get(
                "/api/v1/market/funding-rate-history",
                {"instId": inst_id, "limit": str(min(limit, 100))},
            )
        except (BlofinAPIError, requests.exceptions.RequestException) as exc:
            logger.debug("Funding history unavailable for %s: %s", symbol, exc)
            return []
        records: List[Dict[str, Any]] = []
        for raw in payload.get("data") or []:
            records.append(
                {
                    "funding_rate": self._safe_float(raw.get("fundingRate")),
                    "timestamp": self._safe_int(raw.get("fundingTime")),
                    "next_funding": None,
                }
            )
        return records

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """Convert to float, returning default on bad input/NaN/inf."""
        if value is None:
            return default
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default
        if result != result or result in (float("inf"), float("-inf")):
            return default
        return result

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Convert to int, returning default on bad input."""
        if value is None:
            return default
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default
