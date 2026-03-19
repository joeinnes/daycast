# Context for Cowork

This file documents the database queries available for building historical
context into the daily briefing.

## Recency Window

Stories from the last **3 days** are considered "recent" and are available
via `query_recent(conn, days=3)`. Use these to:

- Identify stories that were covered recently (set `previously_covered: true`)
- Write concise follow-ups rather than re-explaining background
- Detect running stories that should be consolidated

## Historical Context Queries

`query_historical(conn, query_text, days_ago=3)` performs an FTS5 search
against stories **older than** the recency window. The `days_ago` parameter
sets the exclusion boundary: stories from the most recent `days_ago` days are
skipped, so only older stories are returned. Returns at most 3 results,
ordered by date descending.

Use this to:

- Find earlier coverage of a topic for `historical_callback: true` stories
- Surface the `historical_note` — e.g. "First covered 2026-03-01 when..."
- Decide whether a story warrants a "Then & Now" badge

### Example usage

To check if ceasefire talks have been covered before:

```
results = query_historical(conn, "ceasefire", days_ago=3)
```

Each result is a dict-like row with keys: `id`, `date`, `title`, `section`,
`body`, `source`, `previously_covered`, `update_note`, `historical_callback`,
`historical_note`, `hn_url`.

## Database Schema

The `stories` table has:

| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | `{date}-{slugified-title}` |
| date | TEXT | ISO 8601 date |
| title | TEXT | Story headline |
| section | TEXT | Section name (e.g. "World News") |
| body | TEXT | Full story body |
| source | TEXT | Attribution |
| previously_covered | INTEGER | 0 or 1 |
| update_note | TEXT | What's new since last coverage |
| historical_callback | INTEGER | 0 or 1 |
| historical_note | TEXT | Reference to earlier coverage |
| hn_url | TEXT | Hacker News discussion link |

Full-text search is available via `stories_fts` (indexes `title` and `body`).
