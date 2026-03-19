"""Daily briefing build pipeline.

Parses script.md files, generates audio, and builds episode pages.
"""

from __future__ import annotations

import re
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
