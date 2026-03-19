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
    """Generate audio via edge-tts and return word-level timing data.

    Makes up to two attempts (one automatic retry) so that a transient
    network hiccup does not immediately fail the pipeline.  On the first
    failure the exception is logged and the call is retried; on the second
    failure the exception propagates to the caller.
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


def extract_chapters(
    parsed: dict[str, Any], timings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match story titles to word-level timing data and return chapter list.

    Walks *timings* with a forward-only cursor so that body words that
    repeat a title cannot produce false matches.
    """
    stories = _flatten_stories(parsed)

    chapters: list[dict[str, Any]] = []
    cursor = 0
    for idx, story in enumerate(stories):
        title_words = story["title"].split()
        n = len(title_words)
        for i in range(cursor, len(timings) - n + 1):
            if all(
                timings[i + j]["text"] == title_words[j] for j in range(n)
            ):
                chapters.append({
                    "id": f"s{idx + 1}",
                    "title": story["title"],
                    "section": story["section"],
                    "start": timings[i]["offset"],
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

    # -- Chapter markers -----------------------------------------------------
    chapter_lines = [
        f'<div class="chapter" data-start="{ch["start"]}">'
        f'{ch["title"]}</div>'
        for ch in chapters
    ]
    chapter_markers = "\n".join(chapter_lines) + "\n" if chapter_lines else ""

    # -- Speed controls ------------------------------------------------------
    speeds = ["0.85", "1", "1.15", "1.3"]
    speed_buttons = "".join(
        f'<button class="speed-btn" data-speed="{s}">{s}x</button>\n'
        for s in speeds
    )

    # -- Transcript ----------------------------------------------------------
    parts: list[str] = []
    _esc = lambda t: html_module.escape(t, quote=False)

    for section in parsed["sections"]:
        parts.append(f'<h2>{_esc(section["title"])}</h2>')
        for story in section["stories"]:
            title = story["title"]
            safe_title = _esc(title)
            story_lines: list[str] = []
            story_lines.append('<div class="story">')
            story_lines.append(f'<h3>{safe_title}</h3>')

            if story.get("previously_covered"):
                story_lines.append('<span class="badge follow-up">Follow-up</span>')
            if story.get("historical_callback"):
                story_lines.append(
                    '<span class="badge then-now">Then &amp; Now</span>'
                )
                note = story.get("historical_note", "")
                if note:
                    story_lines.append(f'<p class="historical-note">{_esc(note)}</p>')

            source = story.get("source")
            if source:
                story_lines.append(f'<p class="source">Source: {_esc(source)}</p>')

            story_lines.append(f'<p>{_esc(story["body"])}</p>')

            thumbs_up_url = _feedback_url(repo, date_str, title, _THUMBS_UP)
            thumbs_down_url = _feedback_url(repo, date_str, title, _THUMBS_DOWN)
            story_lines.append(
                f'<a class="feedback" href="{thumbs_up_url}" target="_blank">{_THUMBS_UP}</a>'
            )
            story_lines.append(
                f'<a class="feedback" href="{thumbs_down_url}" target="_blank">{_THUMBS_DOWN}</a>'
            )

            story_lines.append('</div>')
            parts.append("\n".join(story_lines))

    transcript = "\n".join(parts) + "\n" if parts else ""

    return (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '<meta charset="utf-8">\n'
        f'<title>Daily Briefing \u2014 {date_str}</title>\n'
        '</head>\n'
        '<body>\n'
        f'<h1>Daily Briefing \u2014 {human_date}</h1>\n'
        f'<p>{date_str}</p>\n'
        '<div class="player">\n'
        f'<audio src="audio.mp3" controls></audio>\n'
        f'<div class="speed-controls">\n{speed_buttons}</div>\n'
        '</div>\n'
        f'<div class="chapters">\n{chapter_markers}</div>\n'
        f'<div class="transcript">\n{transcript}</div>\n'
        '</body>\n'
        '</html>'
    )


# ---------------------------------------------------------------------------
# Publishing helpers (ticket day-1595)
# ---------------------------------------------------------------------------

def copy_latest_episode(episode_dir: str | Path, docs_dir: str | Path) -> None:
    """Copy the episode's index.html into *docs_dir*, creating it if needed."""
    docs_dir = Path(docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(episode_dir) / "index.html", docs_dir / "index.html")


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
        f'<li>{ep["date"]} ({ep["duration_estimate"]}) '
        f'— <a href="../episodes/{ep["date"]}/index.html">Listen</a></li>'
        for ep in episodes
    )

    return (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '<meta charset="utf-8">\n'
        '<title>Archive</title>\n'
        '</head>\n'
        '<body>\n'
        '<h1>Episode Archive</h1>\n'
        f'<ul>\n{items}\n</ul>\n'
        '</body>\n'
        '</html>'
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
        html = render_episode_page(parsed, chapters, {"repo": "..."})
        write_chapters(chapters, Path(episode_dir) / "chapters.json")
        Path(episode_dir, "index.html").write_text(html)
    except Exception as e:
        _log.error("Page rendering failed: %s", e)
        return

    # 5. Copy and publish
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
