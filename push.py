"""Push classified bookmarks to Google Sheets — Recent + Older tabs.

Two worksheets: "Recent" for tweets posted on/after CUTOFF_ISO, "Older"
for everything before. Upsert-by-tweet-ID per worksheet keeps your hand-
edits intact: existing rows are NEVER overwritten — only new tweet IDs
are appended.

Schema in column order: ID | Date | Author | Topic | Summary | Text |
URL | Done (checkbox) | Notes.
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

CUTOFF_ISO = "2025-09-01T00:00:00.000Z"

HEADER = [
    "ID",
    "Date",
    "Author",
    "Topic",
    "Image",
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
    bid, created_at, author, topic, summary, text, url, media_url = b
    image_cell = f'=IMAGE("{media_url}")' if media_url else ""
    return [
        bid,
        (created_at or "")[:10],
        f"@{author}" if author else "",
        topic or "",
        image_cell,
        summary or "",
        text or "",
        url or "",
        False,
        "",
    ]


def _last_col_letter(n: int) -> str:
    return chr(ord("A") + n - 1)


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


def _get_or_create_ws(ss, title: str):
    """Return the worksheet by title, migrating Sheet1 → Recent if needed."""
    existing = {w.title: w for w in ss.worksheets()}
    if title in existing:
        return existing[title]
    if title == "Recent" and "Sheet1" in existing:
        sheet1 = existing["Sheet1"]
        sheet1.update_title("Recent")
        return sheet1
    return ss.add_worksheet(title=title, rows=1000, cols=len(HEADER) + 1)


def _upsert(ss, ws, db_rows: list, label: str) -> None:
    values = ws.get_all_values()
    has_header = bool(values) and values[0] == HEADER

    if not has_header:
        ws.clear()
        rows = [HEADER] + [_row(b) for b in db_rows]
        ws.update(
            values=rows, range_name="A1", value_input_option="USER_ENTERED"
        )
        ws.freeze(rows=1)
        ws.format(
            f"A1:{_last_col_letter(len(HEADER))}1",
            {"textFormat": {"bold": True}},
        )
        _set_done_checkboxes(ss, ws, len(db_rows))
        print(f"  [{label}] initial write: {len(db_rows)} rows")
        return

    existing_ids = {row[0] for row in values[1:] if row and row[0]}
    to_append = [_row(b) for b in db_rows if b[0] not in existing_ids]
    if not to_append:
        print(f"  [{label}] no new ({len(values) - 1} rows)")
        return

    ws.append_rows(to_append, value_input_option="USER_ENTERED")
    new_total = len(values) - 1 + len(to_append)
    _set_done_checkboxes(ss, ws, new_total)
    print(f"  [{label}] appended {len(to_append)} (now {new_total})")


def main() -> None:
    sa_file = os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]
    sheet_id = _sheet_id(os.environ["GOOGLE_SHEET_ID"])
    creds = Credentials.from_service_account_file(sa_file, scopes=SCOPES)
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(sheet_id)

    db = sqlite3.connect(DB_PATH)
    db_rows = db.execute(
        """
        SELECT id, created_at, author_username, topic, summary, text, url,
               media_url
        FROM bookmarks
        ORDER BY topic IS NULL, topic, created_at DESC
        """
    ).fetchall()

    recent = [r for r in db_rows if (r[1] or "") >= CUTOFF_ISO]
    older = [r for r in db_rows if (r[1] or "") < CUTOFF_ISO]
    print(
        f"DB: {len(db_rows)} total "
        f"({len(recent)} recent, {len(older)} older)"
    )

    _upsert(ss, _get_or_create_ws(ss, "Recent"), recent, "Recent")
    _upsert(ss, _get_or_create_ws(ss, "Older"), older, "Older")


if __name__ == "__main__":
    main()
