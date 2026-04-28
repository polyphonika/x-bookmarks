"""Push classified bookmarks to a Google Sheet.

Usage: python push.py
Replaces the contents of the first worksheet with: header row + every bookmark.
"""
import os
import sqlite3
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv()

DB_PATH = Path("bookmarks.db")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = [
    "Date",
    "Author",
    "Topic",
    "Summary",
    "Text",
    "URL",
    "Notes",
]


def main() -> None:
    sa_file = os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    creds = Credentials.from_service_account_file(sa_file, scopes=SCOPES)
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(sheet_id).sheet1

    db = sqlite3.connect(DB_PATH)
    rows = db.execute(
        """
        SELECT created_at, author_username, topic, summary, text, url
        FROM bookmarks
        ORDER BY topic IS NULL, topic, created_at DESC
        """
    ).fetchall()

    values = [HEADER]
    for created_at, author, topic, summary, text, url in rows:
        values.append(
            [
                created_at[:10],
                f"@{author}" if author else "",
                topic or "",
                summary or "",
                text or "",
                url or "",
                "",
            ]
        )

    ws.clear()
    ws.update(values=values, range_name="A1")
    ws.freeze(rows=1)
    ws.format("A1:G1", {"textFormat": {"bold": True}})
    print(f"Pushed {len(rows)} bookmarks to sheet {sheet_id}")


if __name__ == "__main__":
    main()
