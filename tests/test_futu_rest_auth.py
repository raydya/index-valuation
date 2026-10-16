import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from index_valuation.domain import SourceError
from index_valuation.providers import futu_rest_auth
from index_valuation.providers.futu_rest_auth import FutuRestClient, token_credentials


class Store:
    def __init__(self, value=None):
        self.value = value
        self.reads = 0

    def read(self):
        self.reads += 1
        return self.value

    def write(self, value):
        self.value = value


def credentials(**changes):
    return {
        "client_id": "client",
        "access_token": "old-access",
        "refresh_token": "refresh-secret",
        "expires_at": time.time() + 3600,
        "scope": "quote:read accid:*",
        **changes,
    }


def test_lazy_client_does_not_require_authorization_for_status_or_build(tmp_path):
    store = Store()
    with FutuRestClient(store=store, lock_path=tmp_path / "lock"):
        assert store.reads == 0


def test_refresh_preserves_refresh_token_when_provider_omits_it(tmp_path):
    store = Store(credentials(expires_at=0))
    paths = []

    def respond(request):
        paths.append(request.url.path)
        if request.url.path == "/oauth2/token":
            assert b"refresh_token=refresh-secret" in request.content
            return httpx.Response(200, json={"access_token": "new-access", "expires_in": 7200})
        assert request.headers["Authorization"] == "Bearer new-access"
        return httpx.Response(200, json={"ret_code": 0, "data": {}})

    http = httpx.Client(transport=httpx.MockTransport(respond))
    with FutuRestClient(store=store, http=http, lock_path=tmp_path / "lock") as client:
        client.request("GET", "/api/v1.0/quote/US..SPX/history-kline")
    assert paths == ["/oauth2/token", "/api/v1.0/quote/US..SPX/history-kline"]
    assert store.value["refresh_token"] == "refresh-secret"
    assert store.value["scope"] == "quote:read accid:*"
    assert store.value["expires_at"] > time.time() + 7000


def test_unauthorized_quote_refreshes_once_then_preserves_permission_failure(tmp_path):
    store = Store(credentials())
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})
        return httpx.Response(401, json={"code": -12006})

    http = httpx.Client(transport=httpx.MockTransport(respond))
    client = FutuRestClient(store=store, http=http, lock_path=tmp_path / "lock")
    assert client.request("GET", "/api/v1.0/quote/US..NDX/history-kline").status_code == 401
    assert calls.count("/oauth2/token") == 1
    assert len(calls) == 3


def test_concurrent_clients_share_one_refresh_and_use_persisted_token(tmp_path, monkeypatch):
    store = Store(credentials(expires_at=0))
    second_lock_attempt = threading.Event()
    attempts_lock = threading.Lock()
    lock_attempts = 0
    real_flock = futu_rest_auth.fcntl.flock
    refresh_requests = []
    quote_tokens = []

    def observed_flock(file, operation):
        nonlocal lock_attempts
        if operation == futu_rest_auth.fcntl.LOCK_EX:
            with attempts_lock:
                lock_attempts += 1
                if lock_attempts == 2:
                    second_lock_attempt.set()
        return real_flock(file, operation)

    monkeypatch.setattr(futu_rest_auth.fcntl, "flock", observed_flock)

    def respond(request):
        if request.url.path == "/oauth2/token":
            refresh_requests.append(request)
            # Hold the first refresh open until another client attempts the same lock.
            assert second_lock_attempt.wait(5)
            return httpx.Response(
                200,
                json={
                    "access_token": "shared-access",
                    "refresh_token": "rotated-refresh",
                    "expires_in": 3600,
                },
            )
        quote_tokens.append(request.headers["Authorization"])
        return httpx.Response(200, json={"ret_code": 0})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        clients = [
            FutuRestClient(store=store, http=http, lock_path=tmp_path / "shared.lock")
            for _ in range(2)
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [
                pool.submit(client.request, "GET", "/api/v1.0/quote/US..SPX/history-kline")
                for client in clients
            ]
            assert [result.result(timeout=10).status_code for result in results] == [200, 200]

    assert len(refresh_requests) == 1
    assert quote_tokens == ["Bearer shared-access", "Bearer shared-access"]
    assert store.value["access_token"] == "shared-access"
    assert store.value["refresh_token"] == "rotated-refresh"


def test_401_reuses_token_already_refreshed_by_another_client(tmp_path):
    store = Store(credentials())
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/oauth2/token":
            return httpx.Response(
                200,
                json={"access_token": "other-client-access", "expires_in": 3600},
            )
        if request.headers["Authorization"] == "Bearer old-access":
            other_client.refresh()
            return httpx.Response(401, json={"code": -12006})
        assert request.headers["Authorization"] == "Bearer other-client-access"
        return httpx.Response(200, json={"ret_code": 0})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = FutuRestClient(store=store, http=http, lock_path=tmp_path / "shared.lock")
        other_client = FutuRestClient(store=store, http=http, lock_path=tmp_path / "shared.lock")
        assert client.request("GET", "/api/v1.0/quote/US..NDX/history-kline").status_code == 200

    assert calls.count("/oauth2/token") == 1
    assert len(calls) == 3


@pytest.mark.parametrize("scope", ["", "trade:read", "quote:read trade:write", "quote:write"])
def test_rejects_unverified_or_excessive_permissions(scope):
    with pytest.raises(SourceError, match="quote:read"):
        token_credentials(
            {
                "access_token": "secret",
                "refresh_token": "secret",
                "expires_in": 3600,
                "scope": scope,
            },
            "client",
        )


def test_refresh_failure_keeps_stored_credentials_and_does_not_expose_response(tmp_path):
    original = credentials(expires_at=0)
    store = Store(original)
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(400, json={"error": "invalid_grant", "token": "secret-echo"})
        )
    )
    client = FutuRestClient(store=store, http=http, lock_path=tmp_path / "lock")
    with pytest.raises(SourceError, match="refresh failed") as error:
        client.refresh()
    assert "secret-echo" not in str(error.value)
    assert store.value is original


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "https://example.org/api/v1.0/quote/US.FUTU/history-kline"),
        ("POST", "/api/v1.0/trade/place-order"),
        ("POST", "/api/v1.0/quote/modify-user-security"),
        ("GET", "/api/v1.0/quote/../../trade/accounts"),
        ("GET", "/api/v1.0/quote/%2e%2e/%2e%2e/trade/accounts"),
        ("GET", "/api/v1.0/quote/US..SPX%2f..%2f../history-kline"),
        ("GET", "/api/v1.0/quote/US..SPX\\..\\../history-kline"),
        ("GET", "/api/v1.0/quote/US..SPX/history-kline?redirect=trade"),
        ("GET", "/api/v1.0/quote/US..SPX/history-kline#fragment"),
        ("GET", "/api/v1.0/quote/US..SPX/history-kline/"),
        ("GET", "/api/v1.0/quote/US..SPX/unknown-endpoint"),
        ("GET", "/api/v1.0/quote/stock-basicinfo"),
        ("POST", "/api/v1.0/quote/US..SPX/history-kline"),
        ("DELETE", "/api/v1.0/quote/stock-basicinfo"),
    ],
)
def test_client_refuses_non_quote_or_mutating_requests_before_loading_credentials(
    tmp_path, method, path
):
    store = Store(credentials())
    with FutuRestClient(store=store, lock_path=tmp_path / "lock") as client:
        with pytest.raises(SourceError, match="read-only"):
            client.request(method, path)
    assert store.reads == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"headers": {"Authorization": "Bearer overridden"}},
        {"headers": {"Host": "example.org"}},
        {"auth": ("user", "password")},
        {"follow_redirects": True},
        {"cookies": {"session": "override"}},
        {"data": {"code_list": "US..SPX"}},
        {"content": b"override"},
        {"extensions": {}},
    ],
)
def test_client_refuses_request_overrides_before_loading_credentials(tmp_path, kwargs):
    store = Store(credentials())
    with FutuRestClient(store=store, lock_path=tmp_path / "lock") as client:
        with pytest.raises(SourceError, match="read-only"):
            client.request("GET", "/api/v1.0/quote/US..SPX/history-kline", **kwargs)
    assert store.reads == 0


@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        (
            "get",
            "/api/v1.0/quote/US..SPX/history-kline",
            {"params": {"start_time": "2026-10-01"}, "timeout": 7},
        ),
        (
            "GET",
            "/api/v1.0/quote/US..NDX/valuation/detail",
            {"params": {"valuation_type": 1, "interval_type": 3}},
        ),
        (
            "POST",
            "/api/v1.0/quote/stock-basicinfo",
            {"json": {"code_list": ["US..SPX", "US..NDX"]}},
        ),
    ],
)
def test_client_allows_only_known_endpoint_requests(tmp_path, method, path, kwargs):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.host == "webapi.futunn.com"
        assert request.url.path == path
        assert request.method == method.upper()
        assert request.headers["Authorization"] == "Bearer old-access"
        if "params" in kwargs:
            assert dict(request.url.params) == {k: str(v) for k, v in kwargs["params"].items()}
        if "json" in kwargs:
            assert json.loads(request.content) == kwargs["json"]
        if "timeout" in kwargs:
            assert set(request.extensions["timeout"].values()) == {kwargs["timeout"]}
        return httpx.Response(200, json={"ret_code": 0})

    with httpx.Client(transport=httpx.MockTransport(respond), auth=("user", "password")) as http:
        client = FutuRestClient(store=Store(credentials()), http=http, lock_path=tmp_path / "lock")
        assert client.request(method, path, **kwargs).status_code == 200
    assert len(requests) == 1


@pytest.mark.parametrize("expired", [False, True])
def test_client_does_not_follow_quote_or_token_redirects(tmp_path, expired):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://example.org/redirected"})

    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as http:
        store = Store(credentials(expires_at=0) if expired else credentials())
        client = FutuRestClient(store=store, http=http, lock_path=tmp_path / "lock")
        if expired:
            with pytest.raises(SourceError, match="refresh failed"):
                client.request("GET", "/api/v1.0/quote/US..SPX/history-kline")
        else:
            assert client.request("GET", "/api/v1.0/quote/US..SPX/history-kline").status_code == 307

    assert len(requests) == 1
    assert requests[0].url.host == "webapi.futunn.com"
