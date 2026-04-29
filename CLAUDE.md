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
- **Topic discovery is two-phase.** `classify.py discover` proposes 6–10
  buckets from a 50-bookmark sample. The user edits `topics.json` by hand,
  then `classify.py run` classifies every bookmark into that fixed list.
  This keeps the taxonomy human-controlled and prevents long-tail drift.
- **Date cutoff: tweets created on or after 2025-09-01.** Older tweets are
  skipped at pull time. Driven by user intent ("last six months"). Note: this
  filters by *tweet* `created_at`, not bookmark date — the bookmarks endpoint
  doesn't expose bookmark timestamp. So an old tweet bookmarked recently is
  still excluded.
- **Idempotent storage.** `bookmarks.db` is a SQLite file; `INSERT OR IGNORE`
  on tweet ID. Re-running `pull.py` only pulls new bookmarks. `classify.py
  run` only touches rows where `topic IS NULL`.
- **Models: split by phase.** `discover` (one-shot, taxonomy quality matters)
  uses `claude-opus-4-7`. `run` (per-bookmark classification, hundreds of
  calls) uses `claude-haiku-4-5` — short input, structured output is exactly
  what Haiku is for, and the cost difference is ~10× on the volume step.
  Both via `messages.parse()` with Pydantic schemas.

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

```
uv run python pull.py                 # OAuth (first run) + fetch → bookmarks.db
uv run python classify.py discover    # propose topics → topics.json (review by hand!)
uv run python classify.py run         # tag every bookmark with topic + summary
uv run python push.py                 # write to Google Sheet
```

Re-run `pull.py` whenever you want to pick up new bookmarks; then
`classify.py run` to tag the new ones; then `push.py` to refresh the sheet.

## Files (all gitignored)

- `bookmarks.db` — SQLite cache, source of truth
- `tokens.json` — X OAuth access + refresh tokens
- `google_credentials.json` — Google service account key
- `topics.json` — the topic taxonomy (edit by hand between discover and run)
- `.env` — secrets

## Open questions / things to revisit

- **Bookmark-date filtering.** If we want "bookmarked since X" rather than
  "tweet posted since X", we'd need to track which tweet IDs we've seen
  before and stop paginating when we hit one already in the DB. Not worth
  doing unless old-tweet-recently-bookmarked turns out to matter.
- **Pagination cost optimization.** Today we paginate through all bookmarks
  even ones older than the cutoff. Fine at a few hundred bookmarks; if this
  ever covers years of history, short-circuit when an entire page is below
  the cutoff.
- **Topic drift on re-runs.** If you re-bookmark heavily in a new domain,
  `topics.json` may not cover it. Re-run `discover` periodically and merge
  by hand.
