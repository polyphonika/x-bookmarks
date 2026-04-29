"""Push classified bookmarks to a Google Sheet (upsert by tweet ID).

Existing rows in the sheet are NEVER overwritten — your Done checkboxes,
Notes, and any other columns you add survive every re-run. Only NEW
bookmarks (rows whose tweet ID isn't already in the sheet) are appended.

Schema in column order: ID | Date | Author | Topic | Summary | Text | URL |
Done (checkbox) | Notes.
"""
import os
import re
import sqlite3
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv()

DB_PATH = Path("bookmarks.db")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = [
    "ID",
    "Date",
    "Author",
    "Topic",
    "Summary",
    "Text",
    "URL",
    "Done",
    "Notes",
]
DONE_COL_INDEX = HEADER.index("Done")  # 0-based

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


def _sheet_id(value: str) -> str:
    """Accept either a bare sheet ID or a full Google Sheets URL."""
    m = _SHEET_ID_RE.search(value)
    return m.group(1) if m else value.strip()


def _row(b: tuple) -> list:
    bid, created_at, author, topic, summary, text, url = b
    return [
        bid,
        (created_at or "")[:10],
        f"@{author}" if author else "",
        topic or "",
        summary or "",
        text or "",
        url or "",
        False,
        "",
    ]


def _set_done_checkboxes(ss, ws, num_rows: int) -> None:
    """Apply BOOLEAN data validation (checkboxes) to the Done column."""
    if num_rows <= 0:
        return
    ss.batch_update(
        {
            "requests": [
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": ws.id,
                            "startRowIndex": 1,
                            "endRowIndex": 1 + num_rows,
                            "startColumnIndex": DONE_COL_INDEX,
                            "endColumnIndex": DONE_COL_INDEX + 1,
                        },
                        "rule": {
                            "condition": {"type": "BOOLEAN"},
                            "strict": True,
                        },
                    }
                }
            ]
        }
    )


def main() -> None:
    sa_file = os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]
    sheet_id = _sheet_id(os.environ["GOOGLE_SHEET_ID"])
    creds = Credentials.from_service_account_file(sa_file, scopes=SCOPES)
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(sheet_id)
    ws = ss.sheet1

    db = sqlite3.connect(DB_PATH)
    db_rows = db.execute(
        """
        SELECT id, created_at, author_username, topic, summary, text, url
        FROM bookmarks
        ORDER BY topic IS NULL, topic, created_at DESC
        """
    ).fetchall()

    values = ws.get_all_values()
    has_correct_header = bool(values) and values[0] == HEADER

    if not has_correct_header:
        ws.clear()
        rows = [HEADER] + [_row(b) for b in db_rows]
        ws.update(
            values=rows, range_name="A1", value_input_option="USER_ENTERED"
        )
        ws.freeze(rows=1)
        ws.format(f"A1:{chr(ord('A') + len(HEADER) - 1)}1", {"textFormat": {"bold": True}})
        _set_done_checkboxes(ss, ws, len(db_rows))
        print(f"Initial write: {len(db_rows)} bookmarks")
        return

    existing_ids = {row[0] for row in values[1:] if row and row[0]}
    to_append = [_row(b) for b in db_rows if b[0] not in existing_ids]
    if not to_append:
        print(f"No new bookmarks. Sheet has {len(values) - 1} rows.")
        return

    ws.append_rows(to_append, value_input_option="USER_ENTERED")
    new_total = len(values) - 1 + len(to_append)
    _set_done_checkboxes(ss, ws, new_total)
    print(
        f"Appended {len(to_append)} new bookmarks. Sheet now has {new_total} rows."
    )


if __name__ == "__main__":
    main()
