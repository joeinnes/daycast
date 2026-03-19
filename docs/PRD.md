# Product Requirements Document

## Daily Briefing — Personalised News Podcast Pipeline

**Author:** Joe
**Date:** 19 March 2026
**Status:** Draft

## 1. Overview

A fully automated daily news podcast, generated each morning and served via GitHub Pages. A scheduled Cowork job researches and writes a script, runs text-to-speech, builds a self-contained webpage with a podcast player, and pushes to the repo. Historical episodes are archived and indexed in a SQLite database, enabling both repeat-coverage detection and historical callbacks ("last year we covered X, but now Y"). The experience is designed for a single listener (Joe), but is publicly accessible.

## 2. Goals

- Ready to listen by 8am every day
- ~5–10 minutes of audio, length driven by news volume — not padded or truncated artificially
- Personalised story selection that improves over time via feedback
- Historical awareness — surface relevant past coverage as context, not just as a deduplication filter
- Zero ongoing cost (no paid APIs)
- Low maintenance — failures should be self-healing where possible, surfaced clearly where not

## 3. Non-Goals

- Multi-user features, authentication, subscriptions
- A CMS or admin UI
- Mobile app
- Real-time updates or live content

## 4. Architecture

### 4.1 Repository Structure

```
/
├── build.py                  # Core build script
├── briefings.db              # SQLite story archive + FTS index
├── interests.md              # Living taste profile — maintained by Cowork
├── error.log                 # Build errors surfaced here
├── prompts/
│   └── daily-brief.md        # Cowork prompt template (editable)
├── episodes/
│   └── YYYY-MM-DD/
│       ├── script.md         # Cowork-generated script (source of truth)
│       ├── audio.mp3         # edge-tts output
│       ├── chapters.json     # Per-story timestamps for skip
│       └── index.html        # Episode player page
├── docs/                     # GitHub Pages root
│   ├── index.html            # Latest episode (copy of most recent)
│   └── archive.html          # Episode index
└── .github/
    └── ISSUE_TEMPLATE/
        └── feedback.md       # Pre-filled feedback issue template
```

### 4.2 Daily Pipeline

```
07:00  Scheduled Cowork job fires
         │
         ├─ 1. FEEDBACK
         │    Fetch open GitHub issues labelled 'feedback'
         │    Process into interests.md (update taste profile)
         │    Close processed issues via GitHub API
         │
         ├─ 2. RESEARCH
         │    Read interests.md
         │    Query briefings.db: stories from last 3 days (recency filter)
         │    Query briefings.db: FTS search on candidate topics (historical context)
         │    Fetch sources (see §5)
         │    Select and write script → episodes/YYYY-MM-DD/script.md
         │
         └─ 3. BUILD (build.py)
              Parse script.md
              → Insert stories into briefings.db
              → edge-tts → audio.mp3
              → Extract chapter timestamps → chapters.json
              → Render index.html
              → Update docs/ and archive.html
              → git commit + push
              → GitHub Pages serves updated site
```

## 5. Sources

Sources are defined in the Cowork prompt and can be updated without touching build.py.

| Source | Format | Notes |
|--------|--------|-------|
| BBC News | RSS | Mix of UK and World |
| Telex.hu | RSS | Hungarian stories not covered by UK press |
| Hacker News | Official API | Top 30 by score; filtered by interests.md |
| F1 news | RSS (formula1.com or similar) | Always include on race weekends |

**Extensibility:** Adding a new source means editing `prompts/daily-brief.md`. No code changes required.

## 6. The Script

### 6.1 Markdown Schema

`build.py` parses this format. Cowork must conform to it exactly.

```markdown
---
date: 2026-03-19
duration_estimate: 7 minutes
---

# Daily Briefing — Thursday, 19 March 2026

[One-sentence scene-setter. No pleasantries.]

## World News

### Story Title Here
source: BBC News
previously_covered: false

Body text. 2–4 sentences. Written for listening, not reading.
Avoid bullet points, sub-clauses, acronyms without expansion.

### Another Story
source: Telex.hu
previously_covered: true
update_note: Ceasefire agreed after three weeks of conflict
historical_callback: true
historical_note: First covered 2026-03-01 when US-Israeli strikes began

Body text framed as a follow-up, referencing prior context briefly...

## Tech & Developer

### Story Title
source: Hacker News
hn_url: https://news.ycombinator.com/item?id=...

Body text...

## Formula 1

### Story Title
source: formula1.com

Body text...

---
*End of briefing.*
```

Sections are `##` headers — these become story categories in the player UI. Stories are `###` headers — one story per `###`. Frontmatter fields are parsed for metadata. `previously_covered: true` triggers a "Follow-up" badge in the player UI. `historical_callback: true` triggers a "Then & Now" badge and surfaces the historical note in the transcript.

### 6.2 Writing Guidelines (in Cowork prompt)

- Write for listening, not reading — no bullet points, no tables
- Expand all acronyms on first use
- Present tense where possible
- Previously covered stories: briefly state what's new, don't re-explain context
- Historical callbacks: one sentence of past context, then pivot to now — e.g. "When this story first broke in March last year, X was the concern. Today, Y."
- HN stories: include the core technical idea, not just the headline
- 2–4 sentences per story for standard items; up to 6 for lead stories
- Opening line sets the date and tone. No "Good morning Joe" — just begin.

## 7. interests.md

Maintained by Cowork. Updated each morning before writing the script. Not edited manually (though manual overrides are respected).

### Structure

```markdown
# Joe's Interests Profile
Last updated: 2026-03-19

## Always Include
- F1: any news; always include on race weekends even if minor
- Local Hungary news: anything affecting daily life in Budapest
- Jazz framework / local-first sync: any developer discussion
- TypeScript / JS ecosystem: tooling, releases, significant debates

## Strong Interest
- Developer experience and tooling broadly
- AI development (practical applications, not hype)
- UK politics (significant developments, not horse-race)
- Hungarian politics (in terms of impact, not campaign play-by-play)
- Music / audio technology
- Interesting CS / architecture ideas from HN

## Low Interest — Include Only If Significant
- Electoral polling and campaign tactics
- Celebrity or entertainment news
- Crypto / web3
- Pure ML research papers (unless practical implications are clear)

## Inferred Preferences (from feedback)
- Prefers story framing that explains "why this matters" over "what happened"
- Engages more with stories that have a Hungary/Europe angle
- Tends to skip stories about US domestic politics unless globally significant

## Explicit Feedback Notes
- 2026-03-18: liked the Szoboszlai story, short and punchy
- 2026-03-17: Iran war coverage felt repetitive — consolidate when no major new development
```

Cowork updates this file by:

1. Reading processed feedback issues
2. Inferring patterns from the signal: 👍/👎 data over time
3. Adding dated notes for explicit written feedback
4. Revising inferred preference entries periodically based on accumulated signal

## 8. Database (briefings.db)

SQLite database committed to the repo. The markdown `script.md` files remain the canonical source of truth — the DB is a derived index that can be fully rebuilt by replaying all episodes.

### 8.1 Schema

```sql
CREATE TABLE stories (
  id                 TEXT PRIMARY KEY,  -- e.g. "2026-03-19-iran-intel-minister"
  date               TEXT NOT NULL,     -- ISO date: "2026-03-19"
  title              TEXT NOT NULL,
  section            TEXT,              -- e.g. "World News"
  body               TEXT NOT NULL,
  source             TEXT,
  previously_covered INTEGER DEFAULT 0,
  update_note        TEXT,
  historical_callback INTEGER DEFAULT 0,
  historical_note    TEXT,
  hn_url             TEXT
);

CREATE VIRTUAL TABLE stories_fts USING fts5(
  title, body,
  content=stories,
  content_rowid=rowid
);
```

### 8.2 Queries Cowork Uses

**Recency filter** — avoid repeating stories without new developments:

```sql
SELECT id, title, body FROM stories
WHERE date >= date('now', '-3 days')
ORDER BY date DESC;
```

**Historical context** — find past coverage of a topic being considered today:

```sql
SELECT date, title, body FROM stories
JOIN stories_fts ON stories.rowid = stories_fts.rowid
WHERE stories_fts MATCH 'iran war'
  AND date < date('now', '-3 days')
ORDER BY date DESC
LIMIT 3;
```

### 8.3 Rebuild

If `briefings.db` is ever lost or corrupted:

```
python build.py --rebuild-db
```

This replays all `episodes/*/script.md` files and repopulates the database from scratch.

## 9. Audio Generation (build.py)

### 9.1 TTS

- **Tool:** edge-tts (Python package, free, Microsoft neural voices)
- **Voice:** en-GB-RyanNeural (news-reader quality)
- **Input:** full concatenated script text with natural pause markers between stories
- **Output:** audio.mp3

### 9.2 Chapter Timestamps

edge-tts outputs word-level timing data. `build.py` matches story title words against the timing output to find the start timestamp of each story, then writes `chapters.json`:

```json
[
  { "id": "s1", "title": "Iran's intelligence minister killed", "section": "World News", "start": 12.4 },
  { "id": "s2", "title": "Trump threatens gas field strikes", "section": "World News", "start": 87.2 }
]
```

The player uses this for chapter-accurate prev/next skip and progress bar chapter markers.

## 10. The Player (index.html)

Built from a static template by `build.py`. All data (audio path, chapters, transcript) injected at build time. No runtime dependencies.

### Features

- Single audio.mp3 with chapter-based prev/next skip
- Progress bar with visible chapter markers
- Speed control: 0.85×, 1×, 1.15×, 1.3×
- Transcript panel: scrolls in sync with audio, active story highlighted
- Per-story feedback buttons (👍 / 👎) → open pre-filled GitHub issue in new tab
- "Follow-up" badge on `previously_covered: true` stories
- "Then & Now" badge on `historical_callback: true` stories, with historical note surfaced below the story body
- Source attribution per story

### Feedback Issue URL format

```
https://github.com/USER/REPO/issues/new
  ?title=Feedback: 2026-03-19 — Story Title
  &labels=feedback
  &body=Date: 2026-03-19%0AStory: Story Title%0ASignal: 👍%0ANote:
```

## 11. Archive Page (archive.html)

Simple index of all past episodes, newest first. Each entry shows:

- Date
- Section headings (not full story list)
- Duration estimate
- Link to episode player

## 12. Error Handling

### build.py recovery sequence

1. `script.md` parse fails entirely → log error, exit without pushing
2. Individual story parse fails → skip that story, log warning, continue build
3. edge-tts fails → retry once; if still failing, log error and exit without pushing
4. DB insert fails → log warning, continue build (DB can be rebuilt later via `--rebuild-db`)
5. Git push fails → log error, do not retry automatically

All errors written to `error.log` with timestamp. Build only pushes if audio generation succeeded.

### Cowork self-correction

The Cowork prompt instructs Claude to:

- Validate its own markdown against the schema before saving
- If a story can't be adequately summarised (paywalled, broken link), omit it and note it in the script frontmatter
- Never hallucinate story content — if a source is unreachable, skip it
- If a DB query returns no results, proceed without historical context rather than inventing it

## 13. Cowork Prompt Template

Stored in `prompts/daily-brief.md`. Editable without touching code.

Key sections:

1. **Identity** — what this is and who it's for
2. **Interests** — instruction to read interests.md first
3. **Historical context** — instruction to read context.md for recency window and available FTS queries
4. **Sources** — list with fetch instructions per source
5. **Selection criteria** — how to pick and prioritise stories, when to use historical callbacks
6. **Writing style** — how to write for audio
7. **Schema** — exact markdown format required, with field definitions
8. **Output instruction** — save to `episodes/YYYY-MM-DD/script.md`

## 14. Open Questions

- **Scheduling mechanism:** confirm Cowork supports a daily cron-style trigger and can execute `build.py` as a follow-on step after script generation.
- **GitHub API token:** needed for closing feedback issues. Store as a local environment variable. Not committed to repo.

## 15. Out of Scope (v1)

- Personalised voice or custom TTS model
- Push notifications
- Mobile-native app
- Automatic interests.md updates without human-submitted feedback
- Web-based archive search UI (the DB supports it, but no frontend planned)
