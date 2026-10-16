"""Authorize renewable quote-only Futu REST access; no browser is opened automatically."""

import argparse
import base64
import hashlib
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from index_valuation.domain import SourceError
from index_valuation.providers.futu_rest_auth import (
    API_HOST,
    AUTH_PATH,
    REGISTER_PATH,
    TOKEN_PATH,
    FutuRestClient,
    KeychainCredentials,
    token_credentials,
)


def authorize(store, *, port=60355, timeout=1800):
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    redirect = f"http://127.0.0.1:{port}/callback"
    session = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, message):
            body = message.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/authorize":
                self.send_response(302)
                self.send_header("Location", session["url"])
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if parsed.path != "/callback":
                self.reply(404, "Not found")
                return
            query = parse_qs(parsed.query)
            if not secrets.compare_digest(query.get("state", [""])[0], state):
                self.reply(400, "授权会话不匹配，请使用原授权入口。")
            elif "error" in query:
                session["denied"] = True
                self.reply(400, "未获得授权。")
            elif query.get("code") and not session.get("code"):
                session["code"] = query["code"][0]
                self.reply(200, "已收到授权回调。正在保存只读授权并验证自动续期，可以关闭此页。")
            else:
                self.reply(400, "缺少授权码或回调已处理。")

    with HTTPServer(("127.0.0.1", port), Handler) as server, httpx.Client(timeout=25) as http:
        server.timeout = 1
        response = http.post(
            API_HOST + REGISTER_PATH,
            json={
                "redirect_uris": [redirect],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": "Index Valuation - Daily Read-only Data",
                "scope": "quote:read",
            },
        )
        if response.status_code not in (200, 201):
            raise SourceError(f"Futu OAuth registration failed (HTTP {response.status_code})")
        client_id = response.json().get("client_id")
        if not client_id:
            raise SourceError("Futu OAuth registration missing client_id")
        session["url"] = (
            API_HOST
            + AUTH_PATH
            + "?"
            + urlencode(
                {
                    "client_id": client_id,
                    "redirect_uri": redirect,
                    "response_type": "code",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": state,
                    "scope": "quote:read",
                }
            )
        )
        print(
            json.dumps(
                {
                    "status": "waiting_for_authorization",
                    "url": f"http://127.0.0.1:{port}/authorize",
                    "permissions": "quote:read",
                }
            ),
            flush=True,
        )
        deadline = time.monotonic() + timeout
        while not session.get("code") and not session.get("denied") and time.monotonic() < deadline:
            server.handle_request()
        if not session.get("code"):
            raise SourceError("Futu read-only authorization was denied or timed out")
        response = http.post(
            API_HOST + TOKEN_PATH,
            data={
                "grant_type": "authorization_code",
                "code": session.pop("code"),
                "client_id": client_id,
                "redirect_uri": redirect,
                "code_verifier": verifier,
            },
        )
        if response.status_code != 200:
            raise SourceError(f"Futu OAuth token exchange failed (HTTP {response.status_code})")
        store.write(token_credentials(response.json(), client_id))
    with FutuRestClient(store=store) as client:
        client.refresh()
        response = client.request(
            "POST", "/api/v1.0/quote/stock-basicinfo", json={"code_list": ["US..SPX", "US..NDX"]}
        )
        if response.status_code != 200 or response.json().get("ret_code") != 0:
            raise SourceError("Authorization saved, but read-only quote verification failed")
    print(
        json.dumps(
            {
                "status": "authorized",
                "storage": "macOS Keychain",
                "permissions": "quote:read",
                "refresh_verified": True,
            }
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--port", type=int, default=60355)
    args = parser.parse_args()
    store = KeychainCredentials()
    try:
        if args.refresh:
            with FutuRestClient(store=store) as client:
                client.refresh()
            print("Futu REST read-only token refresh succeeded")
        elif args.status:
            credentials = store.read()
            print(
                json.dumps(
                    {
                        "authorized": bool(credentials),
                        "renewable": bool(credentials and credentials.get("refresh_token")),
                        "expires_at": credentials.get("expires_at") if credentials else None,
                    }
                )
            )
        else:
            authorize(store, port=args.port)
    except (SourceError, httpx.HTTPError, ValueError) as error:
        # Never render upstream HTTP bodies, token request fields, or raw exception objects.
        print(str(error) if isinstance(error, SourceError) else "Futu OAuth request failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
