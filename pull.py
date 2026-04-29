"""Fetch X bookmarks via OAuth 2.0 PKCE and cache to SQLite.

Usage: python pull.py
First run prints an authorization URL — open it in your browser, click
Authorize, and the local server captures the callback. Tokens are
persisted to tokens.json. Subsequent runs reuse / refresh the token.

All bookmarks are stored regardless of date; push.py decides which
worksheet they land in.
"""
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
import urllib.parse
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


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


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
    bar = "=" * 78
    print(f"\n{bar}")
    print("Open this URL in your browser, click Authorize:\n")
    print(f"  {auth_url}\n")
    print(f"Listening on {REDIRECT_URI} ...")
    print(f"{bar}\n")

    captured: dict = {"code": None}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            params = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query
            )
            cb_state = params.get("state", [None])[0]
            cb_code = params.get("code", [None])[0]
            if cb_state != state or not cb_code:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"Stale or invalid callback. Close this tab and use "
                    b"the latest URL printed by pull.py."
                )
                print("  (ignored a stale/mismatched callback — still listening)")
                return
            captured["code"] = cb_code
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK \xe2\x80\x94 you can close this tab.")

        def log_message(self, *_a, **_kw):
            pass

    server = HTTPServer(("localhost", 8765), _Handler)
    while captured["code"] is None:
        server.handle_request()

    r = requests.post(
        "https://api.x.com/2/oauth2/token",
        headers={
            "Authorization": f"Basic {_basic_auth()}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": captured["code"],
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


def _pick_media(tw: dict, media_lookup: dict) -> tuple[str | None, str | None]:
    """Return (image_url, media_type) for the first media item, if any.

    For photos we use `url`. For videos and animated GIFs there's no
    direct image, so we use `preview_image_url` (the still frame) — that
    way Sheets =IMAGE() can render a thumbnail.
    """
    keys = tw.get("attachments", {}).get("media_keys", [])
    if not keys:
        return None, None
    m = media_lookup.get(keys[0])
    if not m:
        return None, None
    mtype = m.get("type")
    if mtype == "photo":
        return m.get("url"), mtype
    if mtype in ("video", "animated_gif"):
        return m.get("preview_image_url"), mtype
    return None, mtype


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
          summary TEXT,
          media_url TEXT,
          media_type TEXT
        )
        """
    )
    cols = {row[1] for row in db.execute("PRAGMA table_info(bookmarks)")}
    for col in ("media_url", "media_type"):
        if col not in cols:
            db.execute(f"ALTER TABLE bookmarks ADD COLUMN {col} TEXT")
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
    updated = 0
    pages = 0
    pagination_token: str | None = None

    while True:
        # max_results=25 (not 100) works around an open X API bug where
        # next_token is silently dropped after 2-3 pages at higher page sizes.
        # See: https://devcommunity.x.com/t/bookmarks-api-v2-stops-paginating-after-3-pages-no-next-token-returned/257339
        params: dict = {
            "max_results": 25,
            "tweet.fields": (
                "created_at,author_id,public_metrics,entities,attachments"
            ),
            "expansions": "author_id,attachments.media_keys",
            "user.fields": "username,name",
            "media.fields": "url,preview_image_url,type",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token

        page = _get(
            f"https://api.x.com/2/users/{user_id}/bookmarks", tok, params
        )
        pages += 1
        if pagination_token:
            time.sleep(1)  # gentler on the pagination bug

        includes = page.get("includes", {})
        users = {u["id"]: u for u in includes.get("users", [])}
        media = {m["media_key"]: m for m in includes.get("media", [])}
        data = page.get("data", [])
        if not data:
            break

        for tw in data:
            handle = users.get(tw["author_id"], {}).get("username", "")
            url = f"https://x.com/{handle}/status/{tw['id']}" if handle else ""
            media_url, media_type = _pick_media(tw, media)
            now = datetime.now(timezone.utc).isoformat()
            cur.execute(
                """
                INSERT OR IGNORE INTO bookmarks
                  (id, text, author_id, author_username, created_at, url,
                   raw_json, fetched_at, media_url, media_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tw["id"], tw["text"], tw["author_id"], handle,
                    tw["created_at"], url, json.dumps(tw), now,
                    media_url, media_type,
                ),
            )
            if cur.rowcount:
                inserted += 1
            else:
                cur.execute(
                    """
                    UPDATE bookmarks
                       SET text = ?, raw_json = ?, fetched_at = ?,
                           media_url = ?, media_type = ?
                     WHERE id = ?
                    """,
                    (
                        tw["text"], json.dumps(tw), now,
                        media_url, media_type, tw["id"],
                    ),
                )
                if cur.rowcount:
                    updated += 1

        db.commit()
        pagination_token = page.get("meta", {}).get("next_token")
        if not pagination_token:
            break

    total = db.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
    with_media = db.execute(
        "SELECT COUNT(*) FROM bookmarks WHERE media_url IS NOT NULL"
    ).fetchone()[0]
    print(
        f"Pages: {pages}  inserted: {inserted}  updated: {updated}  "
        f"total in DB: {total}  with media: {with_media}"
    )


if __name__ == "__main__":
    main()
