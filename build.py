"""Daily briefing build pipeline.

Parses script.md files, generates audio, and builds episode pages.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

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


def prepare_tts_text(parsed: dict[str, Any]) -> str:
    """Concatenate intro and all story titles/bodies with pause markers."""
    parts: list[str] = [parsed["intro"]]
    for section in parsed["sections"]:
        for story in section["stories"]:
            parts.append("...")
            parts.append(story["title"])
            parts.append(story["body"])
    return "\n\n".join(parts)


async def generate_audio(
    text: str, output_path: str | Path,
) -> list[dict[str, Any]]:
    """Generate audio via edge-tts with one retry on failure."""
    import edge_tts

    output_path = Path(output_path)

    for attempt in range(2):
        try:
            comm = edge_tts.Communicate(text, voice="en-GB-RyanNeural")
            timings: list[dict[str, Any]] = []
            with open(output_path, "wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        timings.append({
                            "text": chunk["text"],
                            "offset": chunk["offset"] / 10_000_000,
                        })
            return timings
        except Exception:
            if attempt == 1:
                raise


def extract_chapters(
    parsed: dict[str, Any], timings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match story titles to word-level timing data and return chapter list."""
    stories: list[dict[str, Any]] = []
    for section in parsed["sections"]:
        for story in section["stories"]:
            stories.append({
                "title": story["title"],
                "section": section["title"],
            })

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
    """Write chapters list as JSON to the given path."""
    Path(output_path).write_text(
        json.dumps(chapters, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Database layer (ticket day-0e69)
# ---------------------------------------------------------------------------

def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Create/open the briefings database and ensure schema exists."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stories (
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
    )
    conn.execute(
        """CREATE VIRTUAL TABLE IF NOT EXISTS stories_fts
           USING fts5(title, body, content=stories, content_rowid=rowid)"""
    )
    conn.commit()
    return conn


def make_story_id(date_str: str, title: str) -> str:
    """Generate a slugified story ID from *date_str* and *title*."""
    slug = title.lower()
    # Strip non-alphanumeric characters (keep hyphens and spaces).
    slug = re.sub(r"[^a-z0-9 -]", "", slug)
    # Replace spaces with hyphens.
    slug = slug.replace(" ", "-")
    # Collapse multiple consecutive hyphens.
    slug = re.sub(r"-{2,}", "-", slug)
    # Truncate so the full ID stays under 120 chars.
    max_slug = 119 - len(date_str) - 1  # 1 for the joining hyphen
    slug = slug[:max_slug]
    # Strip trailing hyphens left over from truncation.
    slug = slug.rstrip("-")
    return f"{date_str}-{slug}"


def insert_stories(conn: sqlite3.Connection, parsed: dict[str, Any]) -> None:
    """Insert all stories from a *parsed* script into the database."""
    date_str = parsed["date"]
    for section in parsed["sections"]:
        section_title = section["title"]
        for story in section["stories"]:
            story_id = make_story_id(date_str, story["title"])
            conn.execute(
                """INSERT OR IGNORE INTO stories
                   (id, date, title, section, body, source,
                    previously_covered, update_note,
                    historical_callback, historical_note, hn_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    story_id,
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
                ),
            )
            # Populate the FTS index for this story.
            conn.execute(
                """INSERT INTO stories_fts(rowid, title, body)
                   SELECT rowid, title, body FROM stories WHERE id = ?""",
                (story_id,),
            )
    conn.commit()


def query_recent(conn: sqlite3.Connection, days: int = 3) -> list[sqlite3.Row]:
    """Return stories from the last *days* days, ordered by date descending."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    cursor = conn.execute(
        "SELECT * FROM stories WHERE date >= ? ORDER BY date DESC",
        (cutoff,),
    )
    return cursor.fetchall()


def query_historical(
    conn: sqlite3.Connection, query_text: str, days_ago: int = 3,
) -> list[sqlite3.Row]:
    """FTS search for *query_text* among stories older than *days_ago* days."""
    cutoff = (date.today() - timedelta(days=days_ago)).isoformat()
    cursor = conn.execute(
        """SELECT stories.*
           FROM stories_fts
           JOIN stories ON stories.rowid = stories_fts.rowid
           WHERE stories_fts MATCH ?
             AND stories.date < ?
           ORDER BY stories.date DESC
           LIMIT 3""",
        (query_text, cutoff),
    )
    return cursor.fetchall()


def rebuild_db(db_path: str | Path, episodes_dir: str | Path) -> None:
    """Drop all data, re-init the schema, and replay every episode."""
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
