"""Daily briefing build pipeline.

Parses script.md files, generates audio, and builds episode pages.
"""

from __future__ import annotations

import asyncio
import html as html_module
import json
import logging
import re
import shutil
import sqlite3
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import yaml

_log = logging.getLogger(__name__)

# Regex for a metadata line: `word_key: value` before the first blank line.
_METADATA_RE = re.compile(r"^[\w_]+:\s")

# Sentinel patterns that mark the end of the briefing and must be stripped.
_SENTINEL_PATTERNS = [
    re.compile(r"\n---\s*\n\*End of briefing\.\*\s*$"),
    re.compile(r"\n---\s*$"),
    re.compile(r"\*End of briefing\.\*\s*$"),
]

# Metadata field definitions: (field_name, default, coerce_fn | None).
# When coerce_fn is None the raw string value is used as-is.
_FIELD_DEFAULTS: list[tuple[str, Any, Any]] = [
    ("source", None, None),
    ("update_note", None, None),
    ("historical_note", None, None),
    ("hn_url", None, None),
    ("previously_covered", False, lambda v: v.strip().lower() == "true"),
    ("historical_callback", False, lambda v: v.strip().lower() == "true"),
]


def _parse_story_block(lines: list[str]) -> dict[str, Any]:
    """Parse a single story's lines (everything after the ### heading).

    Lines before the first blank line that match ``key: value`` are treated
    as metadata; everything from the first blank line onward (or from the
    first non-metadata line) is the body.
    """
    metadata: dict[str, str] = {}
    body_lines: list[str] = []
    in_body = False

    for line in lines:
        if in_body:
            body_lines.append(line)
        elif line.strip() == "":
            in_body = True
        elif _METADATA_RE.match(line):
            key, _, value = line.partition(":")
            metadata[key.strip()] = value.strip()
        else:
            # Non-metadata, non-blank line before a blank line -- body starts.
            in_body = True
            body_lines.append(line)

    story: dict[str, Any] = {}
    for field, default, coerce in _FIELD_DEFAULTS:
        raw = metadata.get(field)
        if raw is not None and coerce is not None:
            story[field] = coerce(raw)
        elif raw is not None:
            story[field] = raw
        else:
            story[field] = default

    story["body"] = "\n".join(body_lines).strip()
    return story


def _strip_sentinel(body: str) -> str:
    """Remove the trailing ``---`` / ``*End of briefing.*`` sentinel."""
    for pattern in _SENTINEL_PATTERNS:
        body = pattern.sub("", body)
    return body.strip()


def _extract_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Extract and validate YAML frontmatter from *text*.

    Returns the parsed frontmatter dict and the remaining text after the
    closing ``---`` fence.  Raises ``ValueError`` if frontmatter is missing
    or lacks required fields.
    """
    match = re.match(r"^---\n(.*?\n)---(?:\n|$)", text, re.DOTALL)
    if not match:
        raise ValueError("Missing YAML frontmatter")

    frontmatter = yaml.safe_load(match.group(1))
    required = {"date", "duration_estimate"}
    if not isinstance(frontmatter, dict) or not required.issubset(frontmatter):
        raise ValueError("Frontmatter must contain 'date' and 'duration_estimate'")

    return frontmatter, text[match.end():]


def _extract_intro(remainder: str) -> str:
    """Return the intro paragraph between the H1 heading and the first H2."""
    match = re.match(
        r".*?^#\s+[^\n]+\n(.*?)(?=^##\s)",
        remainder,
        re.DOTALL | re.MULTILINE,
    )
    return match.group(1).strip() if match else ""


def _parse_stories(section_body: str) -> list[dict[str, Any]]:
    """Split a section's body on ``###`` headings and parse each story."""
    splits = re.split(r"^###\s+([^\n]+)\n", section_body, flags=re.MULTILINE)
    # splits layout: [preamble, title1, content1, title2, content2, ...]

    stories: list[dict[str, Any]] = []
    for i in range(1, len(splits), 2):
        title = splits[i].strip()
        content = splits[i + 1] if i + 1 < len(splits) else ""

        story = _parse_story_block(content.split("\n"))
        story["title"] = title
        story["body"] = _strip_sentinel(story["body"])
        stories.append(story)

    return stories


def parse_script(path: str | Path) -> dict[str, Any]:
    """Parse a script.md file into a structured dict.

    Returns a dict with keys: ``date``, ``duration_estimate``, ``intro``,
    ``sections``.  Raises ``ValueError`` for missing or malformed frontmatter.
    """
    text = Path(path).read_text(encoding="utf-8")

    frontmatter, remainder = _extract_frontmatter(text)

    # YAML may parse an ISO date as datetime.date -- coerce to string.
    date_value = frontmatter["date"]
    if not isinstance(date_value, str):
        date_value = str(date_value)

    intro = _extract_intro(remainder)

    # Split on ## headings into sections.
    section_splits = re.split(r"^(##\s+[^\n]+)\n", remainder, flags=re.MULTILINE)
    # Layout: [preamble, heading1, content1, heading2, content2, ...]

    sections: list[dict[str, Any]] = []
    for i in range(1, len(section_splits), 2):
        title = section_splits[i].lstrip("#").strip()
        body = section_splits[i + 1] if i + 1 < len(section_splits) else ""
        sections.append({"title": title, "stories": _parse_stories(body)})

    return {
        "date": date_value,
        "duration_estimate": frontmatter["duration_estimate"],
        "intro": intro,
        "sections": sections,
    }


_SLUG_STRIP_RE = re.compile(r"[^a-z0-9 -]")
_SLUG_COLLAPSE_RE = re.compile(r"-{2,}")

_TTS_VOICE = "en-GB-RyanNeural"
_TTS_MAX_ATTEMPTS = 2
_TICKS_PER_SECOND = 10_000_000

_STORIES_DDL = """\
CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY,
    date TEXT,
    title TEXT,
    section TEXT,
    body TEXT,
    source TEXT,
    previously_covered INTEGER,
    update_note TEXT,
    historical_callback INTEGER,
    historical_note TEXT,
    hn_url TEXT
)"""

_FTS_DDL = """\
CREATE VIRTUAL TABLE IF NOT EXISTS stories_fts
USING fts5(title, body, content=stories, content_rowid=rowid)"""


def _flatten_stories(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Yield every story from *parsed*, annotated with its section title.

    Each returned dict has at least ``title`` and ``section`` keys, plus
    whatever keys the story already carries (``body``, metadata, etc.).
    """
    return [
        {**story, "section": section["title"]}
        for section in parsed["sections"]
        for story in section["stories"]
    ]


# ---------------------------------------------------------------------------
# TTS helpers (ticket day-0ae1)
# ---------------------------------------------------------------------------

def prepare_tts_text(parsed: dict[str, Any]) -> str:
    """Concatenate intro and all story titles/bodies with pause markers.

    Stories are separated by ``...`` pause markers so the TTS engine
    produces a brief silence between segments.
    """
    parts: list[str] = [parsed["intro"]]
    for story in _flatten_stories(parsed):
        parts.append("...")
        parts.append(story["title"])
        parts.append(story["body"])
    return "\n\n".join(parts)


async def generate_audio(
    text: str, output_path: str | Path,
) -> list[dict[str, Any]]:
    """Generate audio via edge-tts and return timing data.

    Captures both ``WordBoundary`` and ``SentenceBoundary`` events.
    Returns a list of dicts, each with ``text``, ``offset`` (seconds),
    and ``type`` (``"word"`` or ``"sentence"``).

    Makes up to two attempts (one automatic retry) so that a transient
    network hiccup does not immediately fail the pipeline.
    """
    import edge_tts

    output_path = Path(output_path)

    for attempt in range(_TTS_MAX_ATTEMPTS):
        try:
            comm = edge_tts.Communicate(text, voice=_TTS_VOICE)
            timings: list[dict[str, Any]] = []
            with open(output_path, "wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        timings.append({
                            "text": chunk["text"],
                            "offset": chunk["offset"] / _TICKS_PER_SECOND,
                            "type": "word",
                        })
                    elif chunk["type"] == "SentenceBoundary":
                        timings.append({
                            "text": chunk["text"],
                            "offset": chunk["offset"] / _TICKS_PER_SECOND,
                            "type": "sentence",
                        })
            return timings
        except Exception:
            if attempt + 1 == _TTS_MAX_ATTEMPTS:
                if output_path.exists():
                    output_path.unlink()
                raise
            _log.warning("TTS attempt %d failed; retrying", attempt + 1)

    # Unreachable — the loop always returns or raises — but keeps mypy happy.
    raise AssertionError("unreachable")  # pragma: no cover


_NORM_RE = re.compile(r"[^\w]", re.UNICODE)


def _normalise_word(word: str) -> str:
    """Strip punctuation and normalise apostrophes for fuzzy word matching."""
    # Normalise curly apostrophes to straight before stripping.
    word = word.replace("\u2019", "'").replace("\u2018", "'")
    return _NORM_RE.sub("", word).lower()


def _normalise_sentence(text: str) -> str:
    """Normalise a sentence for fuzzy title matching."""
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    return _NORM_RE.sub("", text).lower()


def extract_chapters(
    parsed: dict[str, Any], timings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match story titles to timing data and return chapter list.

    Supports two modes depending on what the TTS engine provides:

    1. **Sentence boundaries** — each timing entry has full sentence text.
       Titles are matched by normalised containment against sentence text.
    2. **Word boundaries** — each timing entry is a single word.  Titles
       are matched by sliding-window word comparison.

    Both modes walk with a forward-only cursor to prevent false matches
    from repeated words in story bodies.
    """
    stories = _flatten_stories(parsed)

    word_timings = [t for t in timings if t.get("type") != "sentence"]
    sentence_timings = [t for t in timings if t.get("type") == "sentence"]

    # If we have sentence boundaries, prefer them — they're more reliable.
    if sentence_timings:
        return _extract_chapters_from_sentences(stories, sentence_timings)

    # Legacy: fall back to word-level matching (also handles old-style
    # timings without a "type" key).
    if word_timings:
        return _extract_chapters_from_words(stories, word_timings)

    # No usable timings at all — return empty.
    return []


def _extract_chapters_from_sentences(
    stories: list[dict[str, Any]],
    sentence_timings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match story titles against sentence-level timing data."""
    chapters: list[dict[str, Any]] = []
    cursor = 0
    for idx, story in enumerate(stories):
        norm_title = _normalise_sentence(story["title"])
        for i in range(cursor, len(sentence_timings)):
            norm_sentence = _normalise_sentence(sentence_timings[i]["text"])
            if norm_title == norm_sentence or norm_sentence.startswith(norm_title):
                chapters.append({
                    "id": f"s{idx + 1}",
                    "title": story["title"],
                    "section": story["section"],
                    "start": sentence_timings[i]["offset"],
                })
                cursor = i + 1
                break
    return chapters


def _extract_chapters_from_words(
    stories: list[dict[str, Any]],
    word_timings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match story titles against word-level timing data."""
    norm_timings = [_normalise_word(t["text"]) for t in word_timings]

    chapters: list[dict[str, Any]] = []
    cursor = 0
    for idx, story in enumerate(stories):
        title_words = [_normalise_word(w) for w in story["title"].split()]
        n = len(title_words)
        for i in range(cursor, len(word_timings) - n + 1):
            if all(
                norm_timings[i + j] == title_words[j] for j in range(n)
            ):
                chapters.append({
                    "id": f"s{idx + 1}",
                    "title": story["title"],
                    "section": story["section"],
                    "start": word_timings[i]["offset"],
                })
                cursor = i + n
                break

    return chapters


def write_chapters(chapters: list[dict[str, Any]], output_path: str | Path) -> None:
    """Write *chapters* as pretty-printed JSON to *output_path*."""
    Path(output_path).write_text(
        json.dumps(chapters, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Database layer (ticket day-0e69)
# ---------------------------------------------------------------------------

def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Create or open the briefings database and ensure the schema exists.

    Returns a connection with ``row_factory`` set to ``sqlite3.Row`` for
    convenient dict-style access.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(_STORIES_DDL)
    conn.execute(_FTS_DDL)
    conn.commit()
    return conn


def make_story_id(date_str: str, title: str) -> str:
    """Generate a slugified story ID from *date_str* and *title*.

    The resulting ID is always shorter than 120 characters and contains
    only lowercase alphanumerics and hyphens.
    """
    slug = _SLUG_STRIP_RE.sub("", title.lower())
    slug = slug.replace(" ", "-")
    slug = _SLUG_COLLAPSE_RE.sub("-", slug)
    # Truncate so the full ID stays under 120 chars (date + hyphen + slug).
    max_slug = 119 - len(date_str) - 1
    slug = slug[:max_slug].rstrip("-")
    return f"{date_str}-{slug}"


def _story_row(date_str: str, section_title: str, story: dict[str, Any]) -> tuple:
    """Build a parameter tuple for inserting *story* into the stories table."""
    return (
        make_story_id(date_str, story["title"]),
        date_str,
        story["title"],
        section_title,
        story["body"],
        story.get("source"),
        int(story.get("previously_covered", False)),
        story.get("update_note"),
        int(story.get("historical_callback", False)),
        story.get("historical_note"),
        story.get("hn_url"),
    )


def insert_stories(conn: sqlite3.Connection, parsed: dict[str, Any]) -> None:
    """Insert all stories from a *parsed* script into the database.

    Uses ``INSERT OR IGNORE`` so that re-inserting the same episode is a
    safe no-op.  The FTS index is populated via a correlated sub-select
    that only fires when the row actually exists.
    """
    date_str = parsed["date"]
    for section in parsed["sections"]:
        for story in section["stories"]:
            row = _story_row(date_str, section["title"], story)
            story_id = row[0]
            cursor = conn.execute(
                """INSERT OR IGNORE INTO stories
                   (id, date, title, section, body, source,
                    previously_covered, update_note,
                    historical_callback, historical_note, hn_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                row,
            )
            # Populate the FTS index only when a new row was inserted.
            if cursor.rowcount > 0:
                conn.execute(
                    """INSERT INTO stories_fts(rowid, title, body)
                       SELECT rowid, title, body FROM stories WHERE id = ?""",
                    (story_id,),
                )
    conn.commit()


def query_recent(conn: sqlite3.Connection, days: int = 3) -> list[sqlite3.Row]:
    """Return stories from the last *days* days, ordered by date descending."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return conn.execute(
        "SELECT * FROM stories WHERE date >= ? ORDER BY date DESC",
        (cutoff,),
    ).fetchall()


def query_historical(
    conn: sqlite3.Connection, query_text: str, days_ago: int = 3,
) -> list[sqlite3.Row]:
    """FTS search for *query_text* among stories older than *days_ago* days.

    Returns at most three results, ordered by date descending.
    """
    cutoff = (date.today() - timedelta(days=days_ago)).isoformat()
    return conn.execute(
        """SELECT stories.*
           FROM stories_fts
           JOIN stories ON stories.rowid = stories_fts.rowid
           WHERE stories_fts MATCH ?
             AND stories.date < ?
           ORDER BY stories.date DESC
           LIMIT 3""",
        (query_text, cutoff),
    ).fetchall()


def rebuild_db(db_path: str | Path, episodes_dir: str | Path) -> None:
    """Drop all data, re-initialise the schema, and replay every episode.

    Opens a single connection, drops the existing tables, recreates them
    via ``init_db``, then re-inserts every ``*/script.md`` found under
    *episodes_dir*.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TABLE IF EXISTS stories_fts")
    conn.execute("DROP TABLE IF EXISTS stories")
    conn.commit()
    conn.close()

    conn = init_db(db_path)
    for script in sorted(Path(episodes_dir).glob("*/script.md")):
        parsed = parse_script(script)
        insert_stories(conn, parsed)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Episode player page (ticket day-6ce5)
# ---------------------------------------------------------------------------

_THUMBS_UP = "\U0001F44D"
_THUMBS_DOWN = "\U0001F44E"
_EM_DASH = "\u2014"


def _feedback_url(
    repo: str, date_str: str, title: str, emoji: str
) -> str:
    """Build a GitHub issue URL for story-level feedback."""
    encoded_title = quote_plus(title)
    encoded_emoji = quote_plus(emoji)
    return (
        f"https://github.com/{repo}/issues/new?"
        f"title=Feedback:+{date_str}+{_EM_DASH}+{encoded_title}"
        f"&amp;labels=feedback"
        f"&amp;body=Date:+{date_str}%0A"
        f"Story:+{encoded_title}%0A"
        f"Signal:+{encoded_emoji}%0ANote:"
    )


def render_episode_page(
    parsed: dict[str, Any],
    chapters: list[dict[str, Any]],
    config: dict[str, Any],
) -> str:
    """Render a complete HTML player page for an episode.

    *parsed* is the output of ``parse_script``, *chapters* a list of chapter
    dicts with ``id``, ``title``, ``section``, and ``start`` keys, and
    *config* a dict containing at least ``repo``.
    """
    date_str = parsed["date"]
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    human_date = f"{dt.day} {dt.strftime('%B')} {dt.year}"

    repo = config["repo"]
    _esc = lambda t: html_module.escape(t, quote=False)

    # -- Chapter list --------------------------------------------------------
    chapter_items: list[str] = []
    for i, ch in enumerate(chapters):
        start = ch["start"]
        mins = int(start) // 60
        secs = int(start) % 60
        ts = f"{mins}:{secs:02d}"
        chapter_items.append(
            f'<li class="chapter-item" data-start="{start}" role="button" tabindex="0">'
            f'<span class="chapter-num">{i + 1:02d}</span>'
            f'<span class="chapter-info">'
            f'<span class="chapter-title">{_esc(ch["title"])}</span>'
            f'<span class="chapter-section">{_esc(ch["section"])}</span>'
            f'</span>'
            f'<span class="chapter-ts">{ts}</span>'
            f'</li>'
        )

    # -- Chapter marker ticks (for progress bar) -----------------------------
    chapter_ticks: list[str] = []
    for ch in chapters:
        chapter_ticks.append(
            f'<div class="chapter-tick" data-start="{ch["start"]}"></div>'
        )

    # -- Speed controls ------------------------------------------------------
    speeds = ["0.85", "1", "1.15", "1.3"]
    speed_buttons = "".join(
        f'<button class="speed-btn{" active" if s == "1" else ""}" '
        f'data-speed="{s}">{s}x</button>'
        for s in speeds
    )

    # -- Transcript ----------------------------------------------------------
    transcript_parts: list[str] = []

    for section in parsed["sections"]:
        transcript_parts.append(
            f'<h2 class="section-heading">{_esc(section["title"])}</h2>'
        )
        for story in section["stories"]:
            title = story["title"]
            safe_title = _esc(title)
            s: list[str] = []
            s.append('<div class="story">')
            s.append(f'<h3 class="story-title">{safe_title}</h3>')

            # Badges row
            badges: list[str] = []
            if story.get("previously_covered"):
                badges.append(
                    '<span class="badge follow-up">Follow-up</span>'
                )
            if story.get("historical_callback"):
                badges.append(
                    '<span class="badge then-now">Then &amp; Now</span>'
                )
            if badges:
                s.append('<div class="badge-row">' + "".join(badges) + '</div>')

            # Historical note
            if story.get("historical_callback"):
                note = story.get("historical_note", "")
                if note:
                    s.append(
                        f'<blockquote class="historical-note">{_esc(note)}</blockquote>'
                    )

            # Source
            source = story.get("source")
            if source:
                s.append(f'<p class="source">Source: {_esc(source)}</p>')

            # Body
            s.append(f'<p class="story-body">{_esc(story["body"])}</p>')

            # Feedback
            thumbs_up_url = _feedback_url(repo, date_str, title, _THUMBS_UP)
            thumbs_down_url = _feedback_url(repo, date_str, title, _THUMBS_DOWN)
            s.append('<div class="feedback-row">')
            s.append(
                f'<a class="feedback" href="{thumbs_up_url}" '
                f'target="_blank" title="Useful">{_THUMBS_UP}</a>'
            )
            s.append(
                f'<a class="feedback" href="{thumbs_down_url}" '
                f'target="_blank" title="Not useful">{_THUMBS_DOWN}</a>'
            )
            s.append('</div>')

            s.append('</div>')
            transcript_parts.append("\n".join(s))

    transcript_html = "\n".join(transcript_parts) + "\n" if transcript_parts else ""

    # -- Full page assembly --------------------------------------------------
    lines: list[str] = []
    lines.append('<!DOCTYPE html>')
    lines.append('<html lang="en">')
    lines.append('<head>')
    lines.append('<meta charset="utf-8">')
    lines.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    lines.append(f'<title>Daycast {_EM_DASH} {date_str}</title>')
    base_path = '/' + config['repo'].split('/')[-1] + '/'
    lines.append(f'<link rel="alternate" type="application/rss+xml" title="Daycast" href="{base_path}feed.xml">')
    lines.append(
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;500;600'
        '&family=Newsreader:ital,wght@0,400;0,700;1,400&display=swap" rel="stylesheet">'
    )
    lines.append('<style>')
    lines.append("""\
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0c0c0c;--surface:#161616;--elevated:#1e1e1e;
  --text:#e8e0d4;--text-secondary:#8a8279;
  --accent:#c4956a;--accent-hover:#d4a87a;
  --followup:#4a6fa5;--followup-bg:#1a2a3d;
  --thennow:#8a6a9a;--thennow-bg:#2a1a3d;
  --source:#6b8f71;--divider:#2a2a2a;
  --serif:'Newsreader',Georgia,serif;
  --sans:'Hanken Grotesk','Helvetica Neue',Arial,sans-serif;
}
html{font-size:16px;-webkit-font-smoothing:antialiased}
body{
  background:var(--bg);color:var(--text);
  font-family:var(--sans);line-height:1.6;
}
.container{max-width:680px;margin:0 auto;padding:1.5rem 1.25rem 3rem}

/* --- Header --- */
.site-header{
  display:flex;justify-content:space-between;align-items:baseline;
  padding:1.25rem 0;border-bottom:1px solid var(--divider);margin-bottom:2rem;
}
.wordmark{
  font-family:var(--sans);font-weight:600;font-size:.8rem;
  letter-spacing:.18em;text-transform:uppercase;color:var(--accent);
}
.header-date{font-size:.85rem;color:var(--text-secondary)}
.header-iso{font-size:.7rem;color:var(--text-secondary);opacity:.5;margin-left:.5rem}

/* --- Player --- */
.player-card{
  background:var(--surface);border-radius:12px;
  padding:1.5rem;margin-bottom:2rem;
}
audio{width:0;height:0;position:absolute;opacity:0}
.player-controls{display:flex;align-items:center;gap:1rem;margin-bottom:1rem}
.btn-play{
  width:52px;height:52px;border-radius:50%;border:none;cursor:pointer;
  background:var(--accent);color:var(--bg);font-size:1.1rem;
  display:flex;align-items:center;justify-content:center;
  transition:background .15s;flex-shrink:0;
}
.btn-play:hover{background:var(--accent-hover)}
.btn-skip{
  background:none;border:none;color:var(--text-secondary);cursor:pointer;
  font-size:.75rem;padding:.35rem;transition:color .15s;
}
.btn-skip:hover{color:var(--text)}
.progress-wrap{flex:1;display:flex;flex-direction:column;gap:.3rem}
.progress-bar{
  position:relative;width:100%;height:4px;background:var(--divider);
  border-radius:2px;cursor:pointer;
}
.progress-fill{
  height:100%;background:var(--accent);border-radius:2px;width:0%;
  pointer-events:none;transition:width .1s linear;
}
.chapter-tick{
  position:absolute;top:-2px;width:2px;height:8px;
  background:var(--text-secondary);opacity:.45;border-radius:1px;
  pointer-events:none;
}
.time-row{display:flex;justify-content:space-between;font-size:.7rem;color:var(--text-secondary)}

/* --- Speed --- */
.speed-row{display:flex;gap:.4rem;justify-content:center;margin-top:.5rem}
.speed-btn{
  font-family:var(--sans);font-size:.72rem;font-weight:500;
  padding:.3rem .65rem;border-radius:999px;border:1px solid var(--divider);
  background:transparent;color:var(--text-secondary);cursor:pointer;
  transition:all .15s;
}
.speed-btn:hover{border-color:var(--accent);color:var(--text)}
.speed-btn.active{
  background:var(--accent);color:var(--bg);border-color:var(--accent);
}

/* --- Chapters --- */
.chapters-section{margin-bottom:2rem}
.chapters-toggle{
  font-family:var(--sans);font-size:.8rem;font-weight:500;
  color:var(--text-secondary);background:none;border:none;cursor:pointer;
  display:flex;align-items:center;gap:.4rem;padding:.5rem 0;
  transition:color .15s;
}
.chapters-toggle:hover{color:var(--text)}
.chapters-toggle .arrow{transition:transform .2s;display:inline-block}
.chapters-toggle.open .arrow{transform:rotate(90deg)}
.chapter-list{
  list-style:none;overflow:hidden;max-height:0;
  transition:max-height .35s ease;
}
.chapter-list.expanded{max-height:2000px}
.chapter-item{
  display:flex;align-items:center;gap:.75rem;
  padding:.6rem .75rem;border-radius:8px;cursor:pointer;
  transition:background .15s;
}
.chapter-item:hover{background:var(--elevated)}
.chapter-item.active{background:var(--elevated);border-left:3px solid var(--accent)}
.chapter-num{
  font-size:.7rem;font-weight:600;color:var(--text-secondary);
  min-width:1.5rem;text-align:right;
}
.chapter-info{display:flex;flex-direction:column;flex:1;min-width:0}
.chapter-title{font-size:.82rem;font-weight:500;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chapter-section{font-size:.68rem;color:var(--text-secondary)}
.chapter-ts{font-size:.72rem;color:var(--text-secondary);font-variant-numeric:tabular-nums}

/* --- Transcript --- */
.transcript{margin-top:1rem}
.section-heading{
  font-family:var(--sans);font-size:.7rem;font-weight:600;
  text-transform:uppercase;letter-spacing:.14em;
  color:var(--text-secondary);padding:1.5rem 0 .75rem;
  border-bottom:1px solid var(--divider);margin-bottom:1rem;
}
.story{
  background:var(--surface);border-radius:10px;
  padding:1.5rem;margin-bottom:1.25rem;
  border-left:3px solid transparent;
  transition:border-color .3s,box-shadow .3s;
}
.story.active{border-left-color:var(--accent);box-shadow:0 0 20px rgba(196,149,106,.06)}
.story-title{
  font-family:var(--serif);font-size:1.25rem;font-weight:700;
  line-height:1.35;margin-bottom:.6rem;color:var(--text);
}
.badge-row{display:flex;gap:.5rem;margin-bottom:.6rem;flex-wrap:wrap}
.badge{
  font-size:.65rem;font-weight:600;text-transform:uppercase;
  letter-spacing:.06em;padding:.2rem .55rem;border-radius:999px;
}
.badge.follow-up{background:var(--followup-bg);color:var(--followup)}
.badge.then-now{background:var(--thennow-bg);color:var(--thennow)}
.historical-note{
  font-family:var(--serif);font-style:italic;font-size:.88rem;
  color:var(--text-secondary);line-height:1.55;
  border-left:2px solid var(--divider);padding:.5rem 0 .5rem 1rem;
  margin:0 0 .75rem;
}
.source{font-size:.75rem;color:var(--source);margin-bottom:.6rem}
.story-body{
  font-family:var(--sans);font-size:.95rem;line-height:1.7;
  color:var(--text);margin-bottom:.75rem;
}
.feedback-row{display:flex;gap:.75rem;padding-top:.25rem}
.feedback{
  text-decoration:none;font-size:1rem;opacity:.25;
  transition:opacity .2s;
}
.feedback:hover{opacity:.85}

/* --- Footer --- */
.site-footer{
  text-align:center;padding:2rem 0 1rem;
  font-size:.65rem;color:var(--text-secondary);opacity:.35;
  border-top:1px solid var(--divider);margin-top:2rem;
}
.site-footer a{color:var(--text-secondary);text-decoration:none}

@media(max-width:480px){
  .container{padding:1rem}
  .player-card{padding:1rem}
  .story{padding:1.1rem}
  .story-title{font-size:1.1rem}
}
""")
    lines.append('</style>')
    lines.append('</head>')
    lines.append('<body>')
    lines.append('<div class="container">')

    # Header
    lines.append('<header class="site-header">')
    lines.append('<span class="wordmark">DAYCAST</span>')
    lines.append(
        f'<span class="header-date">{human_date}'
        f'<span class="header-iso">{date_str}</span></span>'
    )
    lines.append('</header>')

    # Player card
    lines.append('<div class="player-card">')
    lines.append('<audio src="audio.mp3" controls></audio>')
    lines.append('<div class="player-controls">')
    lines.append('<button class="btn-skip" id="prev-ch" title="Previous chapter">\u25c0\u25c0</button>')
    lines.append('<button class="btn-play" id="play-btn" title="Play">\u25b6</button>')
    lines.append('<button class="btn-skip" id="next-ch" title="Next chapter">\u25b6\u25b6</button>')
    lines.append('<div class="progress-wrap">')
    lines.append('<div class="progress-bar" id="progress-bar">')
    lines.append('<div class="progress-fill" id="progress-fill"></div>')
    lines.append("\n".join(chapter_ticks))
    lines.append('</div>')
    lines.append('<div class="time-row"><span id="cur-time">0:00</span><span id="tot-time">0:00</span></div>')
    lines.append('</div>')  # progress-wrap
    lines.append('</div>')  # player-controls
    lines.append(f'<div class="speed-row">{speed_buttons}</div>')
    lines.append('</div>')  # player-card

    # Chapters
    lines.append('<div class="chapters-section">')
    lines.append('<button class="chapters-toggle" id="ch-toggle"><span class="arrow">\u25b6</span> Chapters</button>')
    lines.append('<ul class="chapter-list" id="chapter-list">')
    lines.extend(chapter_items)
    lines.append('</ul>')
    lines.append('</div>')

    # Transcript
    lines.append(f'<div class="transcript">\n{transcript_html}</div>')

    # Footer
    lines.append(
        '<footer class="site-footer">'
        f'<a href="{base_path}archive.html">Archive</a> &middot; Powered by Daycast'
        '</footer>'
    )

    lines.append('</div>')  # container

    # JavaScript
    lines.append('<script>')
    lines.append("""\
(function(){
  var audio = document.querySelector('audio');
  var playBtn = document.getElementById('play-btn');
  var bar = document.getElementById('progress-bar');
  var fill = document.getElementById('progress-fill');
  var curTime = document.getElementById('cur-time');
  var totTime = document.getElementById('tot-time');
  var prevBtn = document.getElementById('prev-ch');
  var nextBtn = document.getElementById('next-ch');
  var toggle = document.getElementById('ch-toggle');
  var chList = document.getElementById('chapter-list');
  var chItems = document.querySelectorAll('.chapter-item');
  var stories = document.querySelectorAll('.story');
  var ticks = document.querySelectorAll('.chapter-tick');

  function fmt(t){
    if(!isFinite(t))return '0:00';
    var m=Math.floor(t/60),s=Math.floor(t%60);
    return m+':'+(s<10?'0':'')+s;
  }

  /* Play / Pause */
  playBtn.addEventListener('click',function(){
    if(audio.paused){audio.play()}else{audio.pause()}
  });
  audio.addEventListener('play',function(){playBtn.textContent='\\u275A\\u275A'});
  audio.addEventListener('pause',function(){playBtn.textContent='\\u25B6'});

  /* Progress */
  audio.addEventListener('timeupdate',function(){
    if(audio.duration){
      fill.style.width=(audio.currentTime/audio.duration*100)+'%';
      curTime.textContent=fmt(audio.currentTime);
    }
    highlightChapter();
  });
  audio.addEventListener('loadedmetadata',function(){
    totTime.textContent=fmt(audio.duration);
    positionTicks();
  });

  /* Seek */
  bar.addEventListener('click',function(e){
    if(audio.duration){
      var r=bar.getBoundingClientRect();
      audio.currentTime=(e.clientX-r.left)/r.width*audio.duration;
    }
  });

  /* Ticks */
  function positionTicks(){
    if(!audio.duration)return;
    ticks.forEach(function(t){
      var s=parseFloat(t.getAttribute('data-start'));
      t.style.left=(s/audio.duration*100)+'%';
    });
  }

  /* Chapter skip */
  function getStarts(){
    var a=[];chItems.forEach(function(c){a.push(parseFloat(c.getAttribute('data-start')))});
    return a;
  }
  prevBtn.addEventListener('click',function(){
    var starts=getStarts(),cur=audio.currentTime,prev=0;
    for(var i=starts.length-1;i>=0;i--){if(starts[i]<cur-1){prev=starts[i];break}}
    audio.currentTime=prev;
  });
  nextBtn.addEventListener('click',function(){
    var starts=getStarts(),cur=audio.currentTime;
    for(var i=0;i<starts.length;i++){if(starts[i]>cur+.5){audio.currentTime=starts[i];return}}
  });

  /* Speed */
  document.querySelectorAll('.speed-btn').forEach(function(b){
    b.addEventListener('click',function(){
      document.querySelectorAll('.speed-btn').forEach(function(x){x.classList.remove('active')});
      b.classList.add('active');
      audio.playbackRate=parseFloat(b.getAttribute('data-speed'));
    });
  });

  /* Chapter list toggle */
  toggle.addEventListener('click',function(){
    toggle.classList.toggle('open');
    chList.classList.toggle('expanded');
  });

  /* Chapter click seek */
  chItems.forEach(function(c){
    c.addEventListener('click',function(){
      audio.currentTime=parseFloat(c.getAttribute('data-start'));
      if(audio.paused)audio.play();
    });
  });

  /* Highlight active chapter & story */
  function highlightChapter(){
    var cur=audio.currentTime,activeIdx=-1;
    var starts=getStarts();
    for(var i=starts.length-1;i>=0;i--){if(cur>=starts[i]){activeIdx=i;break}}
    chItems.forEach(function(c,i){
      if(i===activeIdx){c.classList.add('active')}else{c.classList.remove('active')}
    });
    stories.forEach(function(s,i){
      if(i===activeIdx){
        if(!s.classList.contains('active')){
          s.classList.add('active');
          s.scrollIntoView({behavior:'smooth',block:'nearest'});
        }
      }else{s.classList.remove('active')}
    });
  }
})();
""")
    lines.append('</script>')

    lines.append('</body>')
    lines.append('</html>')

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Publishing helpers (ticket day-1595)
# ---------------------------------------------------------------------------

def copy_latest_episode(episode_dir: str | Path, docs_dir: str | Path) -> None:
    """Copy the episode into *docs_dir*, creating it if needed.

    Copies index.html to ``docs/index.html`` (the landing page) and also
    copies the full episode directory to ``docs/episodes/{date}/`` so that
    GitHub Pages can serve the audio and chapter data.
    """
    episode_dir = Path(episode_dir)
    docs_dir = Path(docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)

    # Landing page — latest episode (HTML + audio so player works)
    shutil.copy2(episode_dir / "index.html", docs_dir / "index.html")
    audio_src = episode_dir / "audio.mp3"
    if audio_src.exists():
        shutil.copy2(audio_src, docs_dir / "audio.mp3")

    # Full episode directory for GitHub Pages
    date_name = episode_dir.name
    ep_dest = docs_dir / "episodes" / date_name
    ep_dest.mkdir(parents=True, exist_ok=True)
    for f in ("index.html", "audio.mp3", "chapters.json"):
        src = episode_dir / f
        if src.exists():
            shutil.copy2(src, ep_dest / f)


def render_archive(episodes_dir: str | Path) -> str:
    """Scan *episodes_dir* for episodes and return an HTML archive page.

    Episodes are sorted newest-first.  Each entry shows the date, duration
    estimate, and a link to the episode player page.
    """
    episodes_dir = Path(episodes_dir)
    episodes: list[dict[str, Any]] = []

    for subdir in episodes_dir.iterdir():
        script = subdir / "script.md"
        if subdir.is_dir() and script.exists():
            try:
                parsed = parse_script(script)
            except Exception:
                _log.warning("Skipping malformed episode: %s", subdir.name)
                continue
            episodes.append({
                "date": parsed["date"],
                "duration_estimate": parsed["duration_estimate"],
            })

    # Sort newest-first.
    episodes.sort(key=lambda e: e["date"], reverse=True)

    items = "\n".join(
        f'<a href="episodes/{ep["date"]}/index.html" class="ep">'
        f'<span class="ep-date">{ep["date"]}</span>'
        f'<span class="ep-dur">{ep["duration_estimate"]}</span>'
        f'</a>'
        for ep in episodes
    )

    return (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>Daycast Archive</title>\n'
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link href="https://fonts.googleapis.com/css2?family=Hanken+Grotesk:'
        'wght@400;500;600&family=Newsreader:ital,wght@0,400;0,700;1,400&display=swap"'
        ' rel="stylesheet">\n'
        '<style>\n'
        ':root{\n'
        '  --bg:#0c0c0c;--surface:#161616;--text:#e8e0d4;\n'
        '  --text-secondary:#8a8279;--accent:#c4956a;\n'
        '  --divider:#2a2a2a;\n'
        '  --serif:"Newsreader",Georgia,serif;\n'
        '  --sans:"Hanken Grotesk","Helvetica Neue",sans-serif;\n'
        '}\n'
        '*{margin:0;padding:0;box-sizing:border-box}\n'
        'body{background:var(--bg);color:var(--text);font-family:var(--sans);'
        'min-height:100vh;display:flex;flex-direction:column;align-items:center}\n'
        '.wrap{width:100%;max-width:600px;padding:3rem 1.5rem}\n'
        '.header{display:flex;justify-content:space-between;align-items:baseline;'
        'margin-bottom:2.5rem;border-bottom:1px solid var(--divider);padding-bottom:1.5rem}\n'
        '.wordmark{font-size:.8rem;font-weight:600;letter-spacing:.2em;'
        'text-transform:uppercase;color:var(--text-secondary)}\n'
        'h1{font-family:var(--serif);font-size:1.8rem;font-weight:700;'
        'margin-bottom:2rem;color:var(--text)}\n'
        '.episodes{display:flex;flex-direction:column;gap:.75rem}\n'
        '.ep{display:flex;justify-content:space-between;align-items:center;'
        'background:var(--surface);border-radius:8px;padding:1rem 1.25rem;'
        'text-decoration:none;border-left:3px solid transparent;'
        'transition:border-color .2s,background .2s}\n'
        '.ep:hover{border-left-color:var(--accent);background:#1e1e1e}\n'
        '.ep-date{font-family:var(--serif);font-size:1.05rem;color:var(--text)}\n'
        '.ep-dur{font-size:.8rem;color:var(--text-secondary)}\n'
        '.empty{color:var(--text-secondary);font-size:.9rem;font-style:italic}\n'
        '.footer{margin-top:3rem;padding-top:1.5rem;border-top:1px solid var(--divider);'
        'text-align:center;font-size:.7rem;color:var(--text-secondary);'
        'letter-spacing:.05em}\n'
        '</style>\n'
        '</head>\n'
        '<body>\n'
        '<div class="wrap">\n'
        '<div class="header"><span class="wordmark">Daycast</span></div>\n'
        '<h1>Episode Archive</h1>\n'
        f'<div class="episodes">\n{items if items else "<p class=empty>No episodes yet.</p>"}\n</div>\n'
        '<div class="footer">Daycast</div>\n'
        '</div>\n'
        '</body>\n'
        '</html>'
    )


def _fmt_chapter_time(seconds: float) -> str:
    """Format *seconds* as ``HH:MM:SS.mmm`` for Podlove Simple Chapters."""
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _fmt_duration_hhmmss(seconds: float) -> str:
    """Format *seconds* as ``HH:MM:SS`` for iTunes duration."""
    total = max(1, int(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _get_episode_duration(chapters: list[dict], audio_size: int) -> str:
    """Return an HH:MM:SS duration string for a podcast episode.

    Uses chapter start times when available (last chapter start + 30s buffer),
    otherwise estimates from the audio file size with a minimum floor of 1s.
    """
    if chapters:
        last_start = max(ch["start"] for ch in chapters)
        return _fmt_duration_hhmmss(last_start + 30)
    # Rough estimate: ~48 kbps = 6000 bytes/sec
    estimated = audio_size / 6000 if audio_size > 0 else 1
    return _fmt_duration_hhmmss(max(1, estimated))


def render_feed(
    episodes_dir: str | Path,
    config: dict[str, Any],
) -> str:
    """Generate a podcast RSS feed with iTunes and Podlove Simple Chapters.

    Scans *episodes_dir* for episodes (newest-first), reads their
    ``script.md`` and ``chapters.json``, and returns a complete RSS 2.0
    XML string.

    *config* must contain: ``title``, ``description``, ``site_url``,
    ``language``, ``author``.
    """
    episodes_dir = Path(episodes_dir)
    site_url = config["site_url"].rstrip("/")
    title = config["title"]
    description = config["description"]
    language = config["language"]
    author = config["author"]

    episodes: list[dict[str, Any]] = []
    for subdir in episodes_dir.iterdir():
        script = subdir / "script.md"
        if subdir.is_dir() and script.exists():
            try:
                parsed = parse_script(script)
            except Exception:
                _log.warning("Feed: skipping malformed episode %s", subdir.name)
                continue

            chapters: list[dict[str, Any]] = []
            ch_path = subdir / "chapters.json"
            if ch_path.exists():
                try:
                    chapters = json.loads(ch_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            audio_path = subdir / "audio.mp3"
            audio_size = audio_path.stat().st_size if audio_path.exists() else 0

            episodes.append({
                "date": parsed["date"],
                "duration_estimate": parsed["duration_estimate"],
                "intro": parsed.get("intro", ""),
                "chapters": chapters,
                "audio_size": audio_size,
                "stories": _flatten_stories(parsed),
            })

    episodes.sort(key=lambda e: e["date"], reverse=True)

    # Build XML
    items: list[str] = []
    for ep in episodes:
        date_str = ep["date"]
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        pub_date = dt.strftime("%a, %d %b %Y 06:00:00 +0000")
        ep_url = f"{site_url}/episodes/{date_str}"
        audio_url = f"{ep_url}/audio.mp3"
        guid = f"{site_url}/episodes/{date_str}"

        # Build description from story titles
        story_titles = [s["title"] for s in ep["stories"]]
        desc = html_module.escape("; ".join(story_titles)) if story_titles else html_module.escape(ep["intro"])

        # Podlove Simple Chapters
        psc_xml = ""
        if ep["chapters"]:
            ch_items = "\n".join(
                f'      <psc:chapter start="{_fmt_chapter_time(ch["start"])}" title="{html_module.escape(ch["title"])}" />'
                for ch in ep["chapters"]
            )
            psc_xml = f"\n    <psc:chapters version=\"1.2\" xmlns:psc=\"http://podlove.org/simple-chapters\">\n{ch_items}\n    </psc:chapters>"

        items.append(
            f"  <item>\n"
            f"    <title>Daycast \u2014 {date_str}</title>\n"
            f"    <link>{ep_url}/index.html</link>\n"
            f"    <guid isPermaLink=\"true\">{guid}</guid>\n"
            f"    <pubDate>{pub_date}</pubDate>\n"
            f"    <description>{desc}</description>\n"
            f"    <enclosure url=\"{audio_url}\" length=\"{ep['audio_size']}\" type=\"audio/mpeg\" />\n"
            f"    <itunes:duration>{_get_episode_duration(ep['chapters'], ep['audio_size'])}</itunes:duration>\n"
            f"    <itunes:summary>{desc}</itunes:summary>{psc_xml}\n"
            f"  </item>"
        )

    items_xml = "\n".join(items)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"\n'
        '  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"\n'
        '  xmlns:psc="http://podlove.org/simple-chapters"\n'
        '  xmlns:atom="http://www.w3.org/2005/Atom">\n'
        '<channel>\n'
        f'  <title>{html_module.escape(title)}</title>\n'
        f'  <link>{site_url}</link>\n'
        f'  <description>{html_module.escape(description)}</description>\n'
        f'  <language>{language}</language>\n'
        f'  <itunes:author>{html_module.escape(author)}</itunes:author>\n'
        f'  <itunes:category text="News" />\n'
        f'  <atom:link href="{site_url}/feed.xml" rel="self" type="application/rss+xml" />\n'
        f'{items_xml}\n'
        '</channel>\n'
        '</rss>'
    )


def publish(docs_dir: str | Path, audio_ok: bool = True) -> None:
    """Stage, commit, and push the docs directory via git.

    If *audio_ok* is ``False``, skip all git operations.  If ``git push``
    fails, log the error but do not retry or raise.
    """
    if not audio_ok:
        return

    docs_dir = Path(docs_dir)
    subprocess.run(["git", "add", "docs/"])
    subprocess.run(["git", "commit", "-m", "Update published episode"])
    result = subprocess.run(["git", "push"])
    if result.returncode != 0:
        _log.error("git push failed (returncode=%d)", result.returncode)


# ---------------------------------------------------------------------------
# Error handling & logging (ticket day-3075)
# ---------------------------------------------------------------------------

def setup_logging(log_path: str | Path) -> None:
    """Add a FileHandler to the module logger with timestamped formatting."""
    handler = logging.FileHandler(str(log_path))
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _log.addHandler(handler)
    _log.setLevel(logging.DEBUG)


def run_build(episode_dir: str | Path, db_path: str | Path, docs_dir: str | Path) -> None:
    """Main build orchestrator. Calls pipeline functions in order with error handling."""
    # 1. Parse script
    try:
        script_path = Path(episode_dir) / "script.md"
        parsed = parse_script(script_path)
    except Exception as e:
        _log.error("Script parse failed: %s", e)
        return

    # 2. DB insert (non-fatal)
    try:
        conn = init_db(db_path)
        insert_stories(conn, parsed)
    except Exception as e:
        _log.warning("DB insert failed: %s", e)

    # 3. TTS audio generation
    try:
        tts_text = prepare_tts_text(parsed)
        audio_path = Path(episode_dir) / "audio.mp3"
        timings = asyncio.run(generate_audio(tts_text, audio_path))
    except Exception as e:
        _log.error("Audio generation failed: %s", e)
        return

    # 4. Extract chapters and render page
    try:
        chapters = extract_chapters(parsed, timings)
        html = render_episode_page(parsed, chapters, {"repo": "joeinnes/daycast"})
        write_chapters(chapters, Path(episode_dir) / "chapters.json")
        Path(episode_dir, "index.html").write_text(html)
    except Exception as e:
        _log.error("Page rendering failed: %s", e)
        return

    # 5. Generate RSS feed
    feed_config = {
        "repo": "joeinnes/daycast",
        "title": "Daycast",
        "description": "A daily news briefing, automatically generated.",
        "site_url": "https://joeinnes.github.io/daycast",
        "language": "en-gb",
        "author": "Daycast",
    }
    try:
        episodes_root = Path(episode_dir).parent
        feed_xml = render_feed(episodes_root, feed_config)
        Path(docs_dir, "feed.xml").write_text(feed_xml, encoding="utf-8")
    except Exception as e:
        _log.warning("Feed generation failed: %s", e)

    # 6. Copy and publish
    copy_latest_episode(episode_dir, docs_dir)
    publish(docs_dir, audio_ok=True)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python build.py <episode_dir>")
        sys.exit(1)

    episode = Path(sys.argv[1])
    setup_logging(episode / "error.log")
    run_build(
        episode_dir=episode,
        db_path=Path("briefings.db"),
        docs_dir=Path("docs"),
    )
