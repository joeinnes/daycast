"""Daily briefing build pipeline.

Parses script.md files, generates audio, and builds episode pages.
"""

import re
from pathlib import Path

import yaml


# Metadata fields with their types and defaults.
_BOOL_FIELDS = {"previously_covered": False, "historical_callback": False}
_STR_FIELDS = {"source": None, "update_note": None, "historical_note": None, "hn_url": None}


def _coerce_bool(value: str) -> bool:
    """Convert a YAML-ish bool string to a Python bool."""
    return value.strip().lower() == "true"


def _parse_story_block(lines: list[str]) -> dict:
    """Parse a single story's lines (everything after the ### heading)."""
    metadata: dict[str, str] = {}
    body_lines: list[str] = []
    in_body = False

    for line in lines:
        if in_body:
            body_lines.append(line)
        elif line.strip() == "":
            # First blank line marks the transition to body.
            in_body = True
        elif re.match(r"^[\w_]+:\s", line):
            key, _, value = line.partition(":")
            metadata[key.strip()] = value.strip()
        else:
            # Non-metadata, non-blank line before a blank line — treat as body.
            in_body = True
            body_lines.append(line)

    # Build the story dict with defaults.
    story: dict = {}
    for field, default in _STR_FIELDS.items():
        story[field] = metadata.get(field, default)
    for field, default in _BOOL_FIELDS.items():
        if field in metadata:
            story[field] = _coerce_bool(metadata[field])
        else:
            story[field] = default

    story["body"] = "\n".join(body_lines).strip()
    return story


def _strip_sentinel(body: str) -> str:
    """Remove the trailing --- / *End of briefing.* sentinel."""
    body = re.sub(r"\n---\s*\n\*End of briefing\.\*\s*$", "", body)
    body = re.sub(r"\n---\s*$", "", body)
    body = re.sub(r"\*End of briefing\.\*\s*$", "", body)
    return body.strip()


def parse_script(path: str | Path) -> dict:
    """Parse a script.md file into a structured dict.

    Returns a dict with keys: date, duration_estimate, intro, sections.
    Raises ValueError for missing or malformed frontmatter.
    """
    text = Path(path).read_text(encoding="utf-8")

    # 1. Extract YAML frontmatter.
    fm_match = re.match(r"^---\n(.*?\n)---\n", text, re.DOTALL)
    if not fm_match:
        raise ValueError("Missing YAML frontmatter")

    frontmatter = yaml.safe_load(fm_match.group(1))
    if not isinstance(frontmatter, dict) or "date" not in frontmatter or "duration_estimate" not in frontmatter:
        raise ValueError("Frontmatter must contain 'date' and 'duration_estimate'")

    # YAML may parse date as a datetime.date — coerce to string.
    date_val = frontmatter["date"]
    if not isinstance(date_val, str):
        date_val = str(date_val)

    remainder = text[fm_match.end():]

    # 2. Extract intro: text between the H1 heading and the first ##.
    h1_match = re.match(r".*?^#\s+[^\n]+\n(.*?)(?=^##\s)", remainder, re.DOTALL | re.MULTILINE)
    intro = h1_match.group(1).strip() if h1_match else ""

    # 3. Split into ## sections.
    section_splits = re.split(r"^(##\s+[^\n]+)\n", remainder, flags=re.MULTILINE)
    # section_splits: [preamble, title1, content1, title2, content2, ...]

    sections = []
    for i in range(1, len(section_splits), 2):
        sec_title = section_splits[i].lstrip("#").strip()
        sec_content = section_splits[i + 1] if i + 1 < len(section_splits) else ""

        # 4. Split into ### stories within this section.
        story_splits = re.split(r"^###\s+([^\n]+)\n", sec_content, flags=re.MULTILINE)
        # story_splits: [preamble, title1, content1, ...]

        stories = []
        for j in range(1, len(story_splits), 2):
            story_title = story_splits[j].strip()
            story_content = story_splits[j + 1] if j + 1 < len(story_splits) else ""
            story_lines = story_content.split("\n")

            story = _parse_story_block(story_lines)
            story["title"] = story_title

            # Strip sentinel from body if present.
            story["body"] = _strip_sentinel(story["body"])

            stories.append(story)

        sections.append({"title": sec_title, "stories": stories})

    return {
        "date": date_val,
        "duration_estimate": frontmatter["duration_estimate"],
        "intro": intro,
        "sections": sections,
    }
