# x-bookmarks

A personal pipeline that pulls X (Twitter) bookmarks via the API, tags each
one with a topic and a one-line summary using Claude, and writes the result
to a Google Sheet you can read on any device.

## Why

X bookmarks are a write-only list. They pile up, you can't filter them, and
you can't tell at a glance what's worth coming back to. This pipeline turns
that pile into a sortable, filterable sheet with topic colour-coding,
inline image thumbnails, a `Done` checkbox, and a `Notes` column — so you
can sit down with a tablet and actually read through what you've saved.

## Shape

```
X API  ──pull.py──▶  bookmarks.db  ──classify.py──▶  topics + summaries
                       (SQLite)                          │
                                                         └──push.py──▶  Google Sheet
                                                                          ├─ Recent
                                                                          └─ Older
```

Three small idempotent scripts plus an orchestrator. SQLite is the source
of truth — every script can be re-run safely.

## Sheet schema

`ID | Date | Author | Topic | Image | Summary | Text | URL | Done | Notes`

Two worksheets: **Recent** for tweets posted on or after the cutoff
(`2025-09-01`), **Older** for everything before. `=IMAGE()` formulas
render thumbnails inline. `Done` is a native Sheets checkbox. Hand-edited
`Done` and `Notes` survive every re-run — `push.py` upserts by tweet ID
and never overwrites existing rows.

## Setup

You'll need:

- An X developer app (any pay-per-use tier; the bookmarks endpoint costs
  $0.001 per resource as of April 2026)
- An Anthropic API key
- A Google Cloud service account with the Sheets API enabled, and a
  blank Google Sheet shared with the service account email

Then:

```bash
uv sync                       # creates .venv from uv.lock
cp .env.example .env          # fill in the four credentials
```

In your X dev app, set the OAuth 2.0 redirect URI to
`http://localhost:8765/callback` and enable scopes:
`tweet.read users.read bookmark.read offline.access`.

Save your service-account JSON key as `google_credentials.json` in this
directory and share the target Google Sheet (Editor access) with the
service-account email.

## Run

First-time setup — the manual step is `discover` (you review the topic
taxonomy by hand before classifying):

```bash
uv run python pull.py                # OAuth (first run) + fetch → bookmarks.db
uv run python classify.py discover   # propose topics → topics.json (review!)
uv run python classify.py run        # tag every bookmark
uv run python push.py                # write Recent + Older worksheets
```

Routine refresh after that:

```bash
uv run python all.py                 # pull + classify run + push
```

`all.py` skips `discover` (one-time, manual). Re-runs of `pull.py` short-
circuit after seeing 10 consecutive already-cached bookmark IDs, so a
no-op refresh is cents instead of a dollar. Force a complete re-fetch
(e.g. to refresh image URLs that may have rotated) with:

```bash
uv run python pull.py --full
```

## Cost

| Scenario                                              | Approx.   |
| ----------------------------------------------------- | --------- |
| First-time pull (~1000 bookmarks)                     | ~$1       |
| Initial classification (one-shot)                     | ~$0.50    |
| Routine refresh (`all.py`, no new bookmarks)          | **~$0.01** |
| Routine refresh with a handful of new bookmarks       | **~$0.05** |
| `pull.py --full` to refresh media URLs                | ~$1       |

Pull cost is dominated by the X API (per-resource billing). Classification
uses Claude Haiku 4.5 and is essentially free at this volume; topic
discovery uses Opus 4.7 once.

## Models

- **Topic discovery:** `claude-opus-4-7` — one-shot, taxonomy quality
  matters most.
- **Per-bookmark classification:** `claude-haiku-4-5` — short input,
  structured output, hundreds of calls. ~10× cheaper than Opus on the
  hot path with no quality cost for this task.

Both via `messages.parse()` with Pydantic schemas.

## Notable workarounds

- **Bookmarks pagination bug.** X API silently drops `next_token` after
  ~3 pages with `max_results=100`. We use `max_results=25` and a 1s sleep
  between pages — confirmed to paginate cleanly.
  ([forum thread](https://devcommunity.x.com/t/bookmarks-api-v2-stops-paginating-after-3-pages-no-next-token-returned/257339))
- **Soft 800-bookmark cap.** This endpoint has historically capped around
  800 most-recent bookmarks regardless of pagination. You may simply not
  be able to retrieve very old ones via this API.
- **Pull short-circuit.** Bookmarks come back newest-first, so we
  pre-load known IDs and stop fetching once we've seen 10 consecutive
  already-cached ones. New bookmarks (and re-bookmarks) float to the top,
  so this never misses recent activity.

## Files (gitignored)

- `bookmarks.db` — SQLite cache, source of truth
- `tokens.json` — X OAuth access + refresh tokens
- `google_credentials.json` — Google service account key
- `topics.json` — the topic taxonomy (edit by hand between discover and run)
- `.env` — secrets

## License / privacy

This is a personal tool. The output sheet is private by default (only you
and your service account see it). Don't share `google_credentials.json`,
`tokens.json`, or `.env`.
