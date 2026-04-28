"""Fetch X bookmarks via OAuth 2.0 PKCE and cache to SQLite.

Usage: python pull.py
First run opens a browser for OAuth; tokens are persisted to tokens.json.
Subsequent runs reuse / refresh the token. Only tweets created on or after
CUTOFF_ISO are stored.
"""
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

X_CLIENT_ID = os.environ["X_CLIENT_ID"]
X_CLIENT_SECRET = os.environ["X_CLIENT_SECRET"]
REDIRECT_URI = "http://localhost:8765/callback"
SCOPES = "tweet.read users.read bookmark.read offline.access"

DB_PATH = Path("bookmarks.db")
TOKEN_PATH = Path("tokens.json")

CUTOFF_ISO = "2025-09-01T00:00:00.000Z"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None

    def do_GET(self):  # noqa: N802
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.code = params.get("code", [None])[0]
        _CallbackHandler.state = params.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK \xe2\x80\x94 you can close this tab.")

    def log_message(self, *_args, **_kwargs):
        pass


def _basic_auth() -> str:
    return base64.b64encode(f"{X_CLIENT_ID}:{X_CLIENT_SECRET}".encode()).decode()


def _save_token(tok: dict) -> dict:
    tok["obtained_at"] = int(time.time())
    TOKEN_PATH.write_text(json.dumps(tok, indent=2))
    return tok


def _authorize() -> dict:
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    qs = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": X_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    auth_url = f"https://x.com/i/oauth2/authorize?{qs}"
    print(f"Opening browser for X authorization:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", 8765), _CallbackHandler)
    while _CallbackHandler.code is None:
        server.handle_request()

    if _CallbackHandler.state != state:
        raise RuntimeError("OAuth state mismatch — possible CSRF, aborting.")

    r = requests.post(
        "https://api.x.com/2/oauth2/token",
        headers={
            "Authorization": f"Basic {_basic_auth()}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": _CallbackHandler.code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    r.raise_for_status()
    return _save_token(r.json())


def _refresh(tok: dict) -> dict:
    r = requests.post(
        "https://api.x.com/2/oauth2/token",
        headers={
            "Authorization": f"Basic {_basic_auth()}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
        },
        timeout=30,
    )
    r.raise_for_status()
    return _save_token(r.json())


def _token() -> dict:
    if not TOKEN_PATH.exists():
        return _authorize()
    tok = json.loads(TOKEN_PATH.read_text())
    if tok["obtained_at"] + tok["expires_in"] - 60 < time.time():
        return _refresh(tok)
    return tok


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS bookmarks (
          id TEXT PRIMARY KEY,
          text TEXT,
          author_id TEXT,
          author_username TEXT,
          created_at TEXT,
          url TEXT,
          raw_json TEXT,
          fetched_at TEXT,
          topic TEXT,
          summary TEXT
        )
        """
    )
    db.commit()
    return db


def _get(url: str, tok: dict, params: dict | None = None) -> dict:
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {tok['access_token']}"},
        params=params,
        timeout=30,
    )
    if r.status_code == 429:
        reset = int(r.headers.get("x-rate-limit-reset", time.time() + 60))
        wait = max(reset - int(time.time()), 1) + 2
        print(f"Rate limited; sleeping {wait}s…")
        time.sleep(wait)
        return _get(url, tok, params)
    r.raise_for_status()
    return r.json()


def main() -> None:
    tok = _token()
    me = _get("https://api.x.com/2/users/me", tok)
    user_id = me["data"]["id"]
    print(f"Authed as @{me['data']['username']} (id={user_id})")

    db = _db()
    cur = db.cursor()
    inserted = 0
    skipped_old = 0
    pages = 0
    pagination_token: str | None = None

    while True:
        params: dict = {
            "max_results": 100,
            "tweet.fields": "created_at,author_id,public_metrics,entities",
            "expansions": "author_id",
            "user.fields": "username,name",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token

        page = _get(
            f"https://api.x.com/2/users/{user_id}/bookmarks", tok, params
        )
        pages += 1

        users = {u["id"]: u for u in page.get("includes", {}).get("users", [])}
        data = page.get("data", [])
        if not data:
            break

        for tw in data:
            if tw["created_at"] < CUTOFF_ISO:
                skipped_old += 1
                continue
            handle = users.get(tw["author_id"], {}).get("username", "")
            url = f"https://x.com/{handle}/status/{tw['id']}" if handle else ""
            cur.execute(
                """
                INSERT OR IGNORE INTO bookmarks
                  (id, text, author_id, author_username, created_at, url,
                   raw_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tw["id"],
                    tw["text"],
                    tw["author_id"],
                    handle,
                    tw["created_at"],
                    url,
                    json.dumps(tw),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            if cur.rowcount:
                inserted += 1

        db.commit()
        pagination_token = page.get("meta", {}).get("next_token")
        if not pagination_token:
            break

    total = db.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
    print(
        f"Pages: {pages}  inserted: {inserted}  "
        f"skipped (pre-{CUTOFF_ISO[:10]}): {skipped_old}  total in DB: {total}"
    )


if __name__ == "__main__":
    main()
