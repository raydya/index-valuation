"""Read-only Futu REST sessions with renewable credentials in macOS Keychain."""

import ctypes
import fcntl
import json
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

from index_valuation.domain import SourceError

API_HOST = "https://webapi.futunn.com"
AUTH_PATH = "/oauth2/authorize/confirm"
REGISTER_PATH = "/oauth2/register"
TOKEN_PATH = "/oauth2/token"

# Add read-only endpoints individually. Full matches exclude encoded separators,
# query strings and path traversal while preserving index codes such as US..SPX.
_QUOTE_ENDPOINTS = {
    "GET": re.compile(
        r"/api/v1\.0/quote/[A-Z]{2}\.\.?[A-Za-z0-9][A-Za-z0-9._-]*/"
        r"(?:history-kline|valuation/detail)"
    ),
    "POST": re.compile(r"/api/v1\.0/quote/stock-basicinfo"),
}
_REQUEST_OPTIONS = {"params", "json", "timeout"}


def check_read_scope(scope):
    capabilities = {s for s in str(scope).split() if not s.startswith("accid:")}
    if capabilities != {"quote:read"}:
        raise SourceError(
            "Futu REST requires only quote:read; authorize again without trading scopes"
        )


class KeychainCredentials:
    """Generic password storage, without putting tokens in argv, files or logs."""

    def __init__(self, service="cn.index-valuation.futu-rest", account="oauth"):
        self.service, self.account = service.encode(), account.encode()

    def _library(self):
        if sys.platform != "darwin":
            raise SourceError("Futu REST credential storage currently requires macOS Keychain")
        lib = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        void, uint = ctypes.c_void_p, ctypes.c_uint32
        lib.SecKeychainFindGenericPassword.argtypes = [
            void,
            uint,
            ctypes.c_char_p,
            uint,
            ctypes.c_char_p,
            ctypes.POINTER(uint),
            ctypes.POINTER(void),
            ctypes.POINTER(void),
        ]
        lib.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        lib.SecKeychainAddGenericPassword.argtypes = [
            void,
            uint,
            ctypes.c_char_p,
            uint,
            ctypes.c_char_p,
            uint,
            void,
            ctypes.POINTER(void),
        ]
        lib.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        lib.SecKeychainItemModifyAttributesAndData.argtypes = [void, void, uint, void]
        lib.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        lib.SecKeychainItemFreeContent.argtypes = [void, void]
        lib.SecKeychainItemFreeContent.restype = ctypes.c_int32
        return lib

    @staticmethod
    def _release(item):
        if item:
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            cf.CFRelease(item)

    def _find(self, lib):
        size, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        result = lib.SecKeychainFindGenericPassword(
            None,
            len(self.service),
            self.service,
            len(self.account),
            self.account,
            ctypes.byref(size),
            ctypes.byref(data),
            ctypes.byref(item),
        )
        if result not in (0, -25300):
            raise SourceError(f"Futu REST Keychain is unavailable (OSStatus {result})")
        return result, size, data, item

    def read(self):
        lib = self._library()
        result, size, data, item = self._find(lib)
        if result == -25300:
            return None
        try:
            return json.loads(ctypes.string_at(data, size.value).decode())
        except (ValueError, UnicodeError):
            raise SourceError(
                "Invalid Futu REST credentials; run scripts/auth_futu_rest.py"
            ) from None
        finally:
            lib.SecKeychainItemFreeContent(None, data)
            self._release(item)

    def write(self, credentials):
        lib = self._library()
        result, _, data, item = self._find(lib)
        encoded = json.dumps(credentials, separators=(",", ":")).encode()
        try:
            if result == 0:
                lib.SecKeychainItemFreeContent(None, data)
                status = lib.SecKeychainItemModifyAttributesAndData(
                    item, None, len(encoded), encoded
                )
            else:
                status = lib.SecKeychainAddGenericPassword(
                    None,
                    len(self.service),
                    self.service,
                    len(self.account),
                    self.account,
                    len(encoded),
                    encoded,
                    None,
                )
            if status:
                raise SourceError(f"Cannot save Futu REST credentials (OSStatus {status})")
        finally:
            self._release(item)


def token_credentials(payload, client_id, previous=None, *, now=None):
    """Refresh responses may omit the unchanged refresh token and granted scope."""
    previous = previous or {}
    scope = payload.get("scope", previous.get("scope", ""))
    check_read_scope(scope)
    refresh = payload.get("refresh_token") or previous.get("refresh_token")
    try:
        lifetime = int(payload.get("expires_in", 0))
    except (ValueError, TypeError):
        lifetime = 0
    if (
        not payload.get("access_token")
        or not refresh
        or lifetime <= 0
        or payload.get("token_type", "Bearer").lower() != "bearer"
    ):
        raise SourceError("Futu REST did not return a renewable read-only token; authorize again")
    return {
        "client_id": client_id,
        "access_token": payload["access_token"],
        "refresh_token": refresh,
        "scope": scope,
        "expires_at": (time.time() if now is None else now) + lifetime,
    }


class FutuRestClient:
    """Lazy authentication; safe to construct for local status/build commands."""

    def __init__(self, *, timeout=20, store=None, http=None, lock_path=None):
        self.store = store if store is not None else KeychainCredentials()
        self.http = (
            http if http is not None else httpx.Client(timeout=timeout, follow_redirects=False)
        )
        self._owns_http = http is None
        self.lock_path = Path(lock_path or ".cache/futu-rest/auth.lock")

    @contextmanager
    def _lock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _refresh(self, credentials):
        try:
            response = self.http.post(
                API_HOST + TOKEN_PATH,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": credentials["refresh_token"],
                    "client_id": credentials["client_id"],
                },
                auth=None,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise SourceError("Futu REST token refresh network failure") from None
        if response.status_code != 200:
            raise SourceError(
                f"Futu REST token refresh failed (HTTP {response.status_code}); "
                "run scripts/auth_futu_rest.py if authorization expired"
            )
        try:
            payload = response.json()
        except ValueError:
            raise SourceError("Futu REST token refresh returned invalid JSON") from None
        updated = token_credentials(payload, credentials["client_id"], credentials)
        self.store.write(updated)
        return updated

    def _credentials(self, failed_token=None, force=False):
        with self._lock():
            credentials = self.store.read()
            if not credentials:
                raise SourceError("Futu REST authorization missing; run scripts/auth_futu_rest.py")
            check_read_scope(credentials.get("scope", ""))
            if (
                force
                or credentials.get("expires_at", 0) <= time.time() + 120
                or (failed_token is not None and failed_token == credentials.get("access_token"))
            ):
                credentials = self._refresh(credentials)
            return credentials

    def refresh(self):
        self._credentials(force=True)

    def request(self, method, path, **kwargs):
        method = method.upper()
        allowed_path = _QUOTE_ENDPOINTS.get(method)
        if (
            allowed_path is None
            or allowed_path.fullmatch(path) is None
            or not kwargs.keys() <= _REQUEST_OPTIONS
        ):
            raise SourceError("Futu REST client only permits read-only quote requests")
        token = self._credentials()["access_token"]
        for attempt in range(2):
            try:
                response = self.http.request(
                    method,
                    API_HOST + path,
                    headers={"Authorization": "Bearer " + token},
                    auth=None,
                    follow_redirects=False,
                    **kwargs,
                )
            except httpx.HTTPError:
                raise SourceError("Futu REST quote request network failure") from None
            if response.status_code != 401 or attempt:
                return response
            token = self._credentials(failed_token=token)["access_token"]
        raise AssertionError("unreachable")

    def close(self):
        if self._owns_http:
            self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
