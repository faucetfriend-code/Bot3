"""Access-control tests for the FastAPI control interface (audit finding T1).

Safety notes, same as test_api_server.py:

* ``TestClient`` is never used as a context manager, so the lifespan
  handler (which builds real exchange clients) never runs.
* ``bot_integration.initialize`` is patched to a no-op as a second guard,
  and ``trading_bot`` stays ``None`` so every debug route that passes auth
  returns its "not initialized" payload instead of touching an exchange.
* Auth policy is read from ``api_server.config`` at request time, so the
  tests set ``api_token`` / ``api_host`` / ``enable_debug_routes`` on that
  object with ``monkeypatch`` and nothing leaks between tests.
* The token-less path also checks the real connection peer, so the client
  fixture sets the TestClient peer to 127.0.0.1 explicitly (the Starlette
  default peer is the string "testclient", which is correctly NOT loopback;
  there is no production carve-out for it).
"""

import inspect

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock, patch

from trading_bot_v2 import api_server
from trading_bot_v2.config_validation import (
    StartupConfigError,
    check_api_binding,
    run_startup_validation,
)

TOKEN = "correct-horse-battery-staple"
HEADERS = {"X-Api-Token": TOKEN}
LOOPBACK_PEER = ("127.0.0.1", 50000)
REMOTE_PEER = ("10.0.0.5", 50000)

#: Identifiers that must never appear in the read-only /ws handler.
FORBIDDEN_IN_WS = (
    "place_order",
    "publish_event",
    "close_position",
    "cancel_all_orders",
    "_coordinate_signal_execution",
    "emergency_close",
)

#: Identifiers that reach order placement or the synchronous signal path.
#: A GET route whose source mentions any of them is a trading side effect
#: reachable from an unauthenticated read.
FORBIDDEN_IN_GET = (
    "place_order",
    "_coordinate_signal_execution",
    "SIGNAL_GENERATED",
    "publish_event",
    "emergency_close",
    "cancel_all_orders",
    "close_position",
)

SIGNAL_FIRING_DEBUG_ROUTES = [
    "/api/debug/trigger-signals",
    "/api/debug/test-signal-handler",
    "/api/debug/call-actual-generate-signals",
    "/api/debug/call-generate-signals",
]
MUTATING_DEBUG_ROUTES = SIGNAL_FIRING_DEBUG_ROUTES + ["/api/debug/clear-regime-cache"]


@pytest.fixture
def integration():
    """Patch the bot_integration singleton so no real component is built.

    Yields:
        The patched singleton with start/stop replaced by AsyncMocks.
    """
    target = api_server.bot_integration
    with (
        patch.object(target, "initialize", Mock()),
        patch.object(target, "get_status", Mock(return_value={"is_running": False})),
        patch.object(target, "start", AsyncMock()),
        patch.object(target, "stop", AsyncMock()),
        patch.object(target, "trading_bot", None),
        patch.object(api_server, "broadcast_update", AsyncMock()),
    ):
        yield target


@pytest.fixture
def client(integration):
    """Build a TestClient bound to the app without running its lifespan.

    Args:
        integration: The patched bot_integration singleton.

    Returns:
        A TestClient (loopback peer) that never triggers startup handlers.
    """
    return TestClient(api_server.app, client=LOOPBACK_PEER)


@pytest.fixture
def remote_client(integration):
    """Build a TestClient whose connection peer is a non-loopback address.

    Args:
        integration: The patched bot_integration singleton.

    Returns:
        A TestClient presenting 10.0.0.5 as the peer, lifespan not run.
    """
    return TestClient(api_server.app, client=REMOTE_PEER)


@pytest.fixture
def policy(monkeypatch):
    """Return a setter for the live auth policy on api_server.config.

    Args:
        monkeypatch: pytest's monkeypatch fixture (restores on teardown).

    Returns:
        A callable ``set(token=..., host=..., debug=...)``.
    """

    def _set(token=None, host="127.0.0.1", debug=False):
        monkeypatch.setattr(api_server.config, "api_token", token)
        monkeypatch.setattr(api_server.config, "api_host", host)
        monkeypatch.setattr(api_server.config, "enable_debug_routes", debug)

    _set()
    return _set


def _api_routes():
    """Return every APIRoute registered on the app.

    Returns:
        List of APIRoute objects (WebSocket and mount routes excluded).
    """
    return [route for route in api_server.app.routes if isinstance(route, APIRoute)]


def _declares_token_dependency(route: APIRoute) -> bool:
    """Whether a route's dependency tree includes require_api_token.

    Args:
        route: The route to inspect.

    Returns:
        True when require_api_token is among the route-level dependencies.
    """
    return any(
        dep.call is api_server.require_api_token for dep in route.dependant.dependencies
    )


class TestTokenPolicy:
    """Header checks on a representative mutating route (POST /api/bot/start)."""

    def test_anonymous_post_is_401_when_token_set(self, client, integration, policy):
        """Without the header a mutating route is refused before the handler."""
        policy(token=TOKEN)
        response = client.post("/api/bot/start")
        assert response.status_code == 401
        integration.start.assert_not_awaited()

    def test_correct_token_passes_auth(self, client, integration, policy):
        """The right header reaches the (mocked) handler."""
        policy(token=TOKEN)
        response = client.post("/api/bot/start", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["success"] is True
        integration.start.assert_awaited_once()

    def test_wrong_token_is_401(self, client, integration, policy):
        """A wrong header is refused exactly like a missing one."""
        policy(token=TOKEN)
        response = client.post("/api/bot/start", headers={"X-Api-Token": "nope"})
        assert response.status_code == 401
        integration.start.assert_not_awaited()

    def test_token_unset_on_loopback_allows_mutating_routes(
        self, client, integration, policy
    ):
        """No token + loopback bind = local-only server, mutating allowed."""
        policy(token=None, host="127.0.0.1")
        assert client.post("/api/bot/start").status_code == 200
        integration.start.assert_awaited_once()

    @pytest.mark.parametrize("host", ["0.0.0.0", "100.101.93.126", "192.168.1.10"])
    def test_token_unset_off_loopback_is_401_in_dependency(
        self, client, integration, policy, host
    ):
        """Belt-and-braces: the dependency refuses even if startup was bypassed."""
        policy(token=None, host=host)
        assert client.post("/api/bot/start").status_code == 401
        integration.start.assert_not_awaited()

    def test_plain_get_routes_stay_unauthenticated(self, client, policy):
        """Read-only routes outside /api/debug need no token even when set."""
        policy(token=TOKEN)
        assert client.get("/api/status").status_code == 200

    def test_cancel_all_requires_token(self, client, integration, policy):
        """The order-cancelling route is behind the same gate."""
        policy(token=TOKEN)
        assert client.post("/api/orders/cancel-all").status_code == 401


class TestPeerPolicy:
    """The token-less path is decided from the real peer, not from config."""

    def test_token_unset_loopback_peer_no_forwarding_is_allowed(
        self, client, integration, policy
    ):
        """Loopback config + loopback peer + no proxy header = allowed."""
        policy(token=None, host="127.0.0.1")
        assert client.post("/api/bot/start").status_code == 200
        integration.start.assert_awaited_once()

    def test_token_unset_remote_peer_is_401_despite_loopback_config(
        self, remote_client, integration, policy
    ):
        """`uvicorn app --host 0.0.0.0` leaves API_HOST=127.0.0.1; peer wins."""
        policy(token=None, host="127.0.0.1")
        response = remote_client.post("/api/bot/start")
        assert response.status_code == 401
        assert "non-loopback" in response.json()["detail"]
        integration.start.assert_not_awaited()

    def test_default_testclient_peer_is_not_treated_as_loopback(
        self, integration, policy
    ):
        """No carve-out for the Starlette default peer string "testclient"."""
        policy(token=None, host="127.0.0.1")
        default_client = TestClient(api_server.app)
        assert default_client.post("/api/bot/start").status_code == 401
        integration.start.assert_not_awaited()

    @pytest.mark.parametrize(
        "header",
        [
            {"X-Forwarded-For": "203.0.113.9"},
            {"Forwarded": "for=203.0.113.9;proto=https"},
        ],
    )
    def test_token_unset_loopback_peer_with_proxy_header_is_401(
        self, client, integration, policy, header
    ):
        """A reverse proxy or tunnel on loopback must use the token."""
        policy(token=None, host="127.0.0.1")
        response = client.post("/api/bot/start", headers=header)
        assert response.status_code == 401
        assert "proxied" in response.json()["detail"]
        integration.start.assert_not_awaited()

    def test_token_set_correct_header_from_remote_peer_is_allowed(
        self, remote_client, integration, policy
    ):
        """With a token configured the peer no longer matters."""
        policy(token=TOKEN, host="0.0.0.0")
        response = remote_client.post("/api/bot/start", headers=HEADERS)
        assert response.status_code == 200
        integration.start.assert_awaited_once()

    def test_token_set_proxy_header_with_correct_token_is_allowed(
        self, remote_client, integration, policy
    ):
        """Proxied traffic is fine as long as it carries the token."""
        policy(token=TOKEN, host="0.0.0.0")
        headers = dict(HEADERS, **{"X-Forwarded-For": "203.0.113.9"})
        assert remote_client.post("/api/bot/start", headers=headers).status_code == 200

    def test_token_set_missing_header_from_remote_peer_is_401(
        self, remote_client, integration, policy
    ):
        """A remote peer without the token is refused."""
        policy(token=TOKEN, host="0.0.0.0")
        assert remote_client.post("/api/bot/start").status_code == 401
        integration.start.assert_not_awaited()


class TestAsgiStartupHook:
    """The bind/token check runs from the lifespan, not only from __main__."""

    def test_enforce_startup_policy_refuses_open_bind_without_token(self, monkeypatch):
        """API_HOST=0.0.0.0 and no token raises StartupConfigError."""
        monkeypatch.setattr(api_server.config, "api_host", "0.0.0.0")
        monkeypatch.setattr(api_server.config, "api_token", None)
        with pytest.raises(StartupConfigError, match="API_TOKEN"):
            api_server.enforce_startup_policy()

    def test_enforce_startup_policy_passes_with_token(self, monkeypatch):
        """API_HOST=0.0.0.0 with a token is accepted."""
        monkeypatch.setattr(api_server.config, "api_host", "0.0.0.0")
        monkeypatch.setattr(api_server.config, "api_token", TOKEN)
        api_server.enforce_startup_policy()

    def test_enforce_startup_policy_passes_on_loopback_without_token(self, monkeypatch):
        """Loopback without a token is the local-only default and is accepted."""
        monkeypatch.setattr(api_server.config, "api_host", "127.0.0.1")
        monkeypatch.setattr(api_server.config, "api_token", None)
        api_server.enforce_startup_policy()

    def test_lifespan_refuses_open_bind_without_token(self, integration, policy):
        """Entering the app lifespan (what uvicorn does) surfaces the refusal."""
        policy(token=None, host="0.0.0.0")
        with pytest.raises(StartupConfigError, match="API_TOKEN"):
            with TestClient(api_server.app, client=LOOPBACK_PEER):
                pass
        integration.initialize.assert_not_called()

    def test_lifespan_starts_with_token(self, integration, policy):
        """With a token the lifespan proceeds to bot initialisation."""
        policy(token=TOKEN, host="0.0.0.0")
        with patch.object(integration, "shutdown", Mock()):
            with TestClient(api_server.app, client=LOOPBACK_PEER):
                pass
        integration.initialize.assert_called_once()

    def test_lifespan_calls_enforce_startup_policy_first(self):
        """Source-level guard: the lifespan invokes the hook before initialize."""
        source = inspect.getsource(api_server.lifespan)
        assert "enforce_startup_policy()" in source
        assert source.index("enforce_startup_policy()") < source.index(
            "bot_integration.initialize()"
        )


class TestWebSocketReadOnly:
    """/ws streams status and answers ping; it must never route a command."""

    def test_ws_handler_has_no_trading_side_effects(self):
        """The handler source mentions no order-placing or signal identifier."""
        source = inspect.getsource(api_server.websocket_endpoint)
        hits = [name for name in FORBIDDEN_IN_WS if name in source]
        assert hits == [], f"/ws handler reaches trading side effects: {hits}"


class TestStartupRefusal:
    """Token unset + non-loopback host must refuse to start, not just 401."""

    def test_check_api_binding_reports_the_unsafe_combination(self):
        """check_api_binding names the problem for 0.0.0.0 without a token."""
        message = check_api_binding("0.0.0.0", None)
        assert message is not None
        assert "API_TOKEN" in message
        assert check_api_binding("0.0.0.0", TOKEN) is None
        assert check_api_binding("127.0.0.1", None) is None
        assert check_api_binding("localhost", None) is None
        assert check_api_binding("::1", None) is None

    def test_run_startup_validation_raises(self, tmp_path):
        """The validator raises StartupConfigError for the unsafe combination."""
        dotenv = tmp_path / ".env"
        dotenv.write_text("EXCHANGE=blofin\nBLOFIN_DEMO=true\n", encoding="utf-8")
        with pytest.raises(StartupConfigError, match="API_TOKEN"):
            run_startup_validation(str(dotenv), host="0.0.0.0", token=None)

    def test_resolve_bind_address_exits_before_uvicorn(self, monkeypatch, tmp_path):
        """api_server's startup hook turns the refusal into SystemExit(1)."""
        dotenv = tmp_path / ".env"
        dotenv.write_text("EXCHANGE=blofin\nBLOFIN_DEMO=true\n", encoding="utf-8")
        monkeypatch.setattr(api_server.config, "api_host", "0.0.0.0")
        monkeypatch.setattr(api_server.config, "api_token", None)
        monkeypatch.setattr(api_server.config, "api_port", 8000)
        monkeypatch.setattr(
            api_server,
            "run_startup_validation",
            lambda **kw: run_startup_validation(str(dotenv), **kw),
        )
        with pytest.raises(SystemExit) as exc_info:
            api_server.resolve_bind_address()
        assert exc_info.value.code == 1

    def test_resolve_bind_address_returns_configured_binding(
        self, monkeypatch, tmp_path
    ):
        """A loopback bind without a token is accepted and returned as-is."""
        dotenv = tmp_path / ".env"
        dotenv.write_text("EXCHANGE=blofin\nBLOFIN_DEMO=true\n", encoding="utf-8")
        monkeypatch.setattr(api_server.config, "api_host", "127.0.0.1")
        monkeypatch.setattr(api_server.config, "api_token", None)
        monkeypatch.setattr(api_server.config, "api_port", 8123)
        monkeypatch.setattr(
            api_server,
            "run_startup_validation",
            lambda **kw: run_startup_validation(str(dotenv), **kw),
        )
        assert api_server.resolve_bind_address() == ("127.0.0.1", 8123)


class TestDebugRoutes:
    """Signal-firing debug routes: POST only, 404 unless enabled, token required."""

    @pytest.mark.parametrize("path", MUTATING_DEBUG_ROUTES)
    def test_404_when_disabled_even_with_token(self, client, policy, path):
        """A disabled debug route is indistinguishable from a missing one."""
        policy(token=TOKEN, debug=False)
        assert client.post(path, headers=HEADERS).status_code == 404
        assert client.post(path).status_code == 404

    @pytest.mark.parametrize("path", MUTATING_DEBUG_ROUTES)
    def test_requires_token_when_enabled(self, client, policy, path):
        """Once enabled, the route still demands the token."""
        policy(token=TOKEN, debug=True)
        assert client.post(path).status_code == 401
        assert client.post(path, headers={"X-Api-Token": "nope"}).status_code == 401

    @pytest.mark.parametrize("path", MUTATING_DEBUG_ROUTES)
    def test_enabled_with_token_reaches_handler(self, client, policy, path):
        """Enabled + token passes auth; the handler sees no bot and says so."""
        policy(token=TOKEN, debug=True)
        response = client.post(path, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["success"] is False

    @pytest.mark.parametrize("path", SIGNAL_FIRING_DEBUG_ROUTES)
    def test_signal_firing_routes_are_not_get(self, client, policy, path):
        """GET on a converted route is a method error, never a signal."""
        policy(token=TOKEN, debug=True)
        assert client.get(path, headers=HEADERS).status_code == 405

    def test_test_order_route_is_gone(self, client, policy):
        """/api/debug/test-order no longer exists on any method."""
        policy(token=TOKEN, debug=True)
        paths = {route.path for route in _api_routes()}
        assert "/api/debug/test-order" not in paths
        assert client.get("/api/debug/test-order", headers=HEADERS).status_code == 404
        assert client.post("/api/debug/test-order", headers=HEADERS).status_code == 404

    def test_read_only_debug_get_requires_token(self, client, policy):
        """/api/debug/key-config stays GET but exposes config: token required."""
        policy(token=TOKEN)
        assert client.get("/api/debug/key-config").status_code == 401
        assert client.get("/api/debug/key-config", headers=HEADERS).status_code == 200


class TestRouteInvariants:
    """Structural checks over app.routes so a new route cannot regress T1."""

    def test_no_get_route_has_trading_side_effects(self):
        """No GET handler's source mentions an order-placing identifier."""
        offenders = {}
        for route in _api_routes():
            if "GET" not in route.methods:
                continue
            source = inspect.getsource(route.endpoint)
            hits = [name for name in FORBIDDEN_IN_GET if name in source]
            if hits:
                offenders[route.path] = hits
        assert offenders == {}, f"GET routes with trading side effects: {offenders}"

    def test_every_non_get_route_declares_the_token_dependency(self):
        """Every mutating route is behind require_api_token."""
        missing = [
            route.path
            for route in _api_routes()
            if route.methods - {"GET", "HEAD", "OPTIONS"}
            and not _declares_token_dependency(route)
        ]
        assert missing == []

    def test_every_debug_route_declares_the_token_dependency(self):
        """Every /api/debug route, GET included, is behind require_api_token."""
        missing = [
            route.path
            for route in _api_routes()
            if route.path.startswith("/api/debug/")
            and not _declares_token_dependency(route)
        ]
        assert missing == []

    def test_mutating_debug_routes_are_gated_and_not_get(self):
        """The mutating debug routes carry the 404 gate and are POST only."""
        by_path = {route.path: route for route in _api_routes()}
        for path in MUTATING_DEBUG_ROUTES:
            route = by_path[path]
            assert "GET" not in route.methods, path
            calls = [dep.call for dep in route.dependant.dependencies]
            assert api_server.require_debug_routes in calls, path
            assert api_server.require_api_token in calls, path
