# Bookmarks

Personal pipeline that pulls X (Twitter) bookmarks via the API, tags each one
with a topic + a one-line summary using Claude, and writes the result to a
Google Sheet that's filterable from desktop, tablet, or phone.

## Shape

```
X API  ──pull.py──▶  bookmarks.db (SQLite)  ──classify.py──▶  topics + summaries
                                                       │
                                                       └─push.py──▶  Google Sheet
```

Three idempotent scripts. SQLite is the source of truth — re-running any
script is safe.

## Decisions

- **Output surface: Google Sheet.** Cross-device, private, filterable. Other
  options (Notion, Airtable, local SQLite + UI) all add work without giving
  more than the sheet does.
- **Two worksheets: `Recent` + `Older`.** Tweets posted on or after
  `CUTOFF_ISO` (2025-09-01) go to Recent — that's the priority list. Older
  tweets land in Older as a "not a priority but kept" backlog. Cutoff is a
  presentation concern only: pull stores everything; push splits by date.
- **Upsert by tweet ID, never overwrite.** `push.py` matches by ID column
  and only appends rows whose ID isn't already in the sheet. Hand-edited
  Done checkboxes and Notes survive every re-run. The price: a one-time
  clear-and-rewrite when the schema changes (header mismatch detected).
- **Topic discovery is two-phase.** `classify.py discover` proposes 6–10
  buckets from a 50-bookmark sample. The user edits `topics.json` by hand,
  then `classify.py run` classifies every bookmark into that fixed list.
  Keeps the taxonomy human-controlled and prevents long-tail drift.
- **Idempotent storage.** SQLite is the source of truth. `pull.py`
  insert-then-update: existing rows get text/raw_json/media refreshed,
  but `topic` and `summary` are preserved. `classify.py run` only touches
  rows where `topic IS NULL`.
- **Pull short-circuit.** Bookmarks come back newest-first by bookmark
  date, so once `pull.py` sees 10 consecutive IDs it already has, it
  stops paginating. Routine refresh costs pennies (~1 page of 25 calls)
  instead of dollars (~38 pages × 25). New bookmarks always float to the
  top, so this never misses recent activity. Use `python pull.py --full`
  to disable the short-circuit and re-fetch everything (e.g. to refresh
  media URLs that may have rotated).
- **Images via `=IMAGE()`.** For tweets with photos we store the media
  URL; for videos and animated GIFs we store the still-frame
  `preview_image_url`. `push.py` writes `=IMAGE("url")` formulas so
  Sheets renders inline thumbnails on every device.
- **Models: split by phase.** `discover` (one-shot, taxonomy quality
  matters) uses `claude-opus-4-7`. `run` (per-bookmark classification,
  hundreds of calls) uses `claude-haiku-4-5` — short input, structured
  output is exactly what Haiku is for, and the cost difference is ~10× on
  the volume step. Both via `messages.parse()` with Pydantic schemas.

## Cost (X API)

Per April 2026 pricing, owned reads (including bookmarks) are billed at
$0.001 per resource. ~500 bookmarks = $0.50 per full pull. There's an open
issue where bookmarks have been billed at $0.005 instead of $0.001 — treat
the rate as 1–5× advertised until X confirms.

## Setup (one-time)

1. **Python env** (managed by [uv](https://docs.astral.sh/uv/))
   ```
   uv sync           # creates .venv and installs from uv.lock
   cp .env.example .env
   ```
   Run scripts with `uv run python pull.py` (or activate `.venv` once and
   use `python pull.py` directly).

2. **X developer app** at https://developer.x.com:
   - Set redirect URI to `http://localhost:8765/callback`
   - Enable scopes: `tweet.read users.read bookmark.read offline.access`
   - Copy Client ID + Client Secret into `.env`

3. **Anthropic API key**: paste into `ANTHROPIC_API_KEY` in `.env`.

4. **Google Sheets**:
   - Create a service account in Google Cloud, enable the Sheets API
   - Download the JSON key as `google_credentials.json` in this directory
   - Create a blank Google Sheet, copy the long ID from its URL into `.env`
   - **Share the sheet with the service account email** (the
     `...@...iam.gserviceaccount.com` address from the JSON key) — give it
     Editor access. Without this, `push.py` 403s.

## Run

First-time setup (the manual step is `discover` — review topics by hand):

```
uv run python pull.py                 # OAuth (first run) + fetch → bookmarks.db
uv run python classify.py discover    # propose topics → topics.json (review by hand!)
uv run python classify.py run         # tag every bookmark with topic + summary
uv run python push.py                 # write Recent + Older worksheets
```

Routine refresh (every step idempotent):

```
uv run python all.py                  # = pull + classify run + push
```

`all.py` skips `classify discover` because that's a one-time setup. If you
later want to re-derive topics, run `discover` by hand.

## Files (all gitignored)

- `bookmarks.db` — SQLite cache, source of truth
- `tokens.json` — X OAuth access + refresh tokens
- `google_credentials.json` — Google service account key
- `topics.json` — the topic taxonomy (edit by hand between discover and run)
- `.env` — secrets

## Sheet schema

Each worksheet has the same columns:

```
ID | Date | Author | Topic | Image | Summary | Text | URL | Done | Notes
```

- `ID`: anchors the upsert. Hide column A in the sheet UI for cleaner reading.
- `Image`: `=IMAGE("url")` formula. Empty for text-only tweets.
- `Done`: native Sheets checkbox (programmatically set as BOOLEAN data
  validation). Tick when you've processed a bookmark.
- `Notes`: yours. Anything you type here survives every re-run.

## Open questions / things to revisit

- **Pagination bug workaround.** `pull.py` uses `max_results=25` (not 100)
  to dodge a documented X API bug where `next_token` is silently dropped at
  larger page sizes. There's also a soft ~800-bookmark cap on this endpoint.
- **Topic drift on re-runs.** If you re-bookmark heavily in a new domain,
  `topics.json` may not cover it. Re-run `discover` periodically and merge
  by hand.
- **Re-pull cost on first / `--full` runs.** The bookmarks endpoint has
  no `since_id`, so the only way to refresh deep history is to paginate
  from the top. A full pull of ~1000 bookmarks costs ~$1 (or up to $5
  at the buggy 5× rate). Routine incremental runs cost cents thanks to
  the short-circuit.
