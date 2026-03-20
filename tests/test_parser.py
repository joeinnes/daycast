"""Tests for the script.md parser (build.parse_script).

NOTE: We add the project root to sys.path so that `from build import ...`
works when running via `pytest tests/`. This can be removed once a
pyproject.toml with proper packaging is in place.

Spec ambiguities / observations (recorded here since the tick DB is locked):

- The spec says `date` is a string (ISO date). The fixture value is `2026-03-19`.
  We test that it comes back as a plain string, not a datetime object.
- The spec says `previously_covered` is a bool. The fixture uses the YAML-ish
  strings "true" / "false". We expect the parser to coerce these to Python bools.
- The spec does not define behaviour for a script.md that has sections but no
  stories inside a section. We do not test that case.
- The trailing `---` / `*End of briefing.*` sentinel: the spec says it must not
  appear in any story body. We test that explicitly.
- The spec does not say whether `body` should preserve internal newlines or
  collapse them. We test that body is stripped of leading/trailing whitespace
  but otherwise preserve the fixture's single-paragraph format.
"""

import sys
from pathlib import Path

import pytest

# Ensure the project root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE = Path(__file__).parent / "fixtures" / "sample_script.md"


# ---------------------------------------------------------------------------
# Import sanity
# ---------------------------------------------------------------------------

def test_parse_script_is_importable():
    """parse_script should be importable from build."""
    from build import parse_script  # noqa: F401


# ---------------------------------------------------------------------------
# Helper: invoke the parser on the sample fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def parsed():
    from build import parse_script

    return parse_script(FIXTURE)


# ---------------------------------------------------------------------------
# 1. Frontmatter
# ---------------------------------------------------------------------------

def test_frontmatter_date(parsed):
    """Parser returns the date as an ISO-format string."""
    assert parsed["date"] == "2026-03-19"


def test_frontmatter_duration_estimate(parsed):
    """Parser returns the duration_estimate string."""
    assert parsed["duration_estimate"] == "7 minutes"


# ---------------------------------------------------------------------------
# 2. Intro text (scene-setter)
# ---------------------------------------------------------------------------

def test_intro_text(parsed):
    """The intro is the paragraph between the H1 title and the first H2."""
    expected = (
        "A tense day in geopolitics as ceasefire talks resume, while the tech "
        "world debates a controversial new JavaScript proposal."
    )
    assert parsed["intro"] == expected


# ---------------------------------------------------------------------------
# 3. Sections
# ---------------------------------------------------------------------------

def test_section_count(parsed):
    """The fixture has three sections."""
    assert len(parsed["sections"]) == 3


def test_section_titles(parsed):
    """Section titles match the ## headings in order."""
    titles = [s["title"] for s in parsed["sections"]]
    assert titles == ["World News", "Tech & Developer", "Formula 1"]


# ---------------------------------------------------------------------------
# 4. Stories within sections
# ---------------------------------------------------------------------------

def test_world_news_story_count(parsed):
    """World News contains two stories."""
    section = parsed["sections"][0]
    assert len(section["stories"]) == 2


def test_tech_developer_story_count(parsed):
    """Tech & Developer contains one story."""
    section = parsed["sections"][1]
    assert len(section["stories"]) == 1


def test_f1_story_count(parsed):
    """Formula 1 contains one story."""
    section = parsed["sections"][2]
    assert len(section["stories"]) == 1


def test_story_titles_world_news(parsed):
    """Story titles in World News match the ### headings."""
    stories = parsed["sections"][0]["stories"]
    assert stories[0]["title"] == "Iran's intelligence minister killed in Israeli strike"
    assert stories[1]["title"] == "Ceasefire talks resume in Cairo"


def test_story_title_tech(parsed):
    stories = parsed["sections"][1]["stories"]
    assert stories[0]["title"] == "TC39 signals proposal to deprecate prototype inheritance"


def test_story_title_f1(parsed):
    stories = parsed["sections"][2]["stories"]
    assert stories[0]["title"] == "Verstappen tops final practice in Melbourne"


# ---------------------------------------------------------------------------
# 5. Metadata fields on stories
# ---------------------------------------------------------------------------

def test_source_field(parsed):
    """Each story should have its source extracted."""
    story = parsed["sections"][0]["stories"][0]
    assert story["source"] == "BBC News"


def test_previously_covered_false(parsed):
    """previously_covered: false is parsed as Python False."""
    story = parsed["sections"][0]["stories"][0]
    assert story["previously_covered"] is False


def test_previously_covered_true(parsed):
    """previously_covered: true is parsed as Python True."""
    story = parsed["sections"][0]["stories"][1]
    assert story["previously_covered"] is True


def test_update_note(parsed):
    story = parsed["sections"][0]["stories"][1]
    assert story["update_note"] == "Talks resumed after a three-day pause"


def test_historical_callback_true(parsed):
    story = parsed["sections"][0]["stories"][1]
    assert story["historical_callback"] is True


def test_historical_note(parsed):
    story = parsed["sections"][0]["stories"][1]
    assert story["historical_note"] == (
        "First covered 2026-03-01 when US-brokered negotiations began"
    )


def test_hn_url(parsed):
    story = parsed["sections"][1]["stories"][0]
    assert story["hn_url"] == "https://news.ycombinator.com/item?id=12345678"


# ---------------------------------------------------------------------------
# 6. Boolean defaults (False when absent)
# ---------------------------------------------------------------------------

def test_previously_covered_explicit_false(parsed):
    """previously_covered: false in the fixture is parsed as Python False."""
    story = parsed["sections"][2]["stories"][0]
    assert story["previously_covered"] is False


def test_historical_callback_defaults_false(parsed):
    """historical_callback should default to False when not specified."""
    story = parsed["sections"][0]["stories"][0]
    assert story["historical_callback"] is False


# ---------------------------------------------------------------------------
# 7. Optional string fields default to None
# ---------------------------------------------------------------------------

def test_update_note_defaults_none(parsed):
    story = parsed["sections"][0]["stories"][0]
    assert story["update_note"] is None


def test_historical_note_defaults_none(parsed):
    story = parsed["sections"][0]["stories"][0]
    assert story["historical_note"] is None


def test_hn_url_defaults_none(parsed):
    story = parsed["sections"][0]["stories"][0]
    assert story["hn_url"] is None


def test_source_defaults_none_when_absent(tmp_path):
    """If a story omits the source line, it should be None."""
    from build import parse_script

    content = (
        "---\n"
        "date: 2026-01-01\n"
        "duration_estimate: 2 minutes\n"
        "---\n"
        "\n"
        "# Daily Briefing — Thursday, 1 January 2026\n"
        "\n"
        "Scene-setter.\n"
        "\n"
        "## Section\n"
        "\n"
        "### No Source Story\n"
        "\n"
        "Body without a source line.\n"
    )
    script = tmp_path / "script.md"
    script.write_text(content)
    result = parse_script(script)
    assert result["sections"][0]["stories"][0]["source"] is None


# ---------------------------------------------------------------------------
# 8. Body text extraction
# ---------------------------------------------------------------------------

def test_body_text_first_story(parsed):
    story = parsed["sections"][0]["stories"][0]
    expected = (
        "Iran's intelligence minister was killed overnight in a targeted "
        "Israeli airstrike on Tehran. The strike marks a significant "
        "escalation in the shadow war between the two nations. Western "
        "governments have called for restraint, though privately several "
        "officials described the operation as precisely targeted."
    )
    assert story["body"] == expected


def test_body_text_second_story(parsed):
    story = parsed["sections"][0]["stories"][1]
    assert story["body"].startswith("Ceasefire negotiations between Israel")
    assert story["body"].endswith(
        "timeline for Israeli withdrawal from northern Gaza."
    )


def test_body_text_tech_story(parsed):
    story = parsed["sections"][1]["stories"][0]
    assert "stage-one TC39 proposal" in story["body"]


def test_body_text_f1_story(parsed):
    story = parsed["sections"][2]["stories"][0]
    assert "Max Verstappen" in story["body"]
    assert "McLaren" in story["body"]


# ---------------------------------------------------------------------------
# 9. Multiple stories in one section are correctly separated
# ---------------------------------------------------------------------------

def test_stories_do_not_bleed_into_each_other(parsed):
    """The body of the first World News story must not contain text from the
    second story, and vice-versa."""
    first = parsed["sections"][0]["stories"][0]
    second = parsed["sections"][0]["stories"][1]
    assert "Ceasefire" not in first["body"]
    assert "intelligence minister" not in second["body"]


# ---------------------------------------------------------------------------
# 10. Trailing sentinel not included in any story body
# ---------------------------------------------------------------------------

def test_no_end_of_briefing_in_bodies(parsed):
    """The trailing `---` / `*End of briefing.*` must not leak into story
    bodies."""
    for section in parsed["sections"]:
        for story in section["stories"]:
            assert "End of briefing" not in story["body"]
            assert story["body"].strip() != "---"
            assert not story["body"].strip().endswith("---")


# ---------------------------------------------------------------------------
# 11. Error handling: missing / malformed frontmatter
# ---------------------------------------------------------------------------

def test_missing_frontmatter_raises(tmp_path):
    """A file with no YAML frontmatter should raise an error."""
    from build import parse_script

    bad_file = tmp_path / "no_frontmatter.md"
    bad_file.write_text("# Daily Briefing\n\nSome text.\n")
    with pytest.raises(ValueError):
        parse_script(bad_file)


def test_malformed_frontmatter_raises(tmp_path):
    """Frontmatter missing required fields should raise an error."""
    from build import parse_script

    bad_file = tmp_path / "bad_frontmatter.md"
    bad_file.write_text("---\ntitle: oops\n---\n# Daily Briefing\n")
    with pytest.raises(ValueError):
        parse_script(bad_file)


# ---------------------------------------------------------------------------
# Minimal synthetic fixture: story with no optional metadata
# ---------------------------------------------------------------------------

def test_body_line_resembling_metadata_not_consumed(tmp_path):
    """A body paragraph containing 'source: ...' should not be parsed as
    metadata — only lines before the first blank line after ### are metadata."""
    from build import parse_script

    content = (
        "---\n"
        "date: 2026-01-01\n"
        "duration_estimate: 2 minutes\n"
        "---\n"
        "\n"
        "# Daily Briefing — Thursday, 1 January 2026\n"
        "\n"
        "Scene-setter.\n"
        "\n"
        "## Section\n"
        "\n"
        "### Ambiguous Story\n"
        "source: BBC News\n"
        "\n"
        "According to the source: Reuters reported that talks collapsed.\n"
    )
    script = tmp_path / "script.md"
    script.write_text(content)
    result = parse_script(script)
    story = result["sections"][0]["stories"][0]
    assert story["source"] == "BBC News"
    assert "source: Reuters reported" in story["body"]


def test_minimal_story(tmp_path):
    """A story with only a title and body (no metadata lines) should parse
    with all optional fields at their defaults."""
    from build import parse_script

    content = (
        "---\n"
        "date: 2026-01-01\n"
        "duration_estimate: 3 minutes\n"
        "---\n"
        "\n"
        "# Daily Briefing — Thursday, 1 January 2026\n"
        "\n"
        "Scene-setter paragraph.\n"
        "\n"
        "## Section One\n"
        "\n"
        "### Bare Story\n"
        "\n"
        "Just a body.\n"
    )
    script = tmp_path / "script.md"
    script.write_text(content)
    result = parse_script(script)

    story = result["sections"][0]["stories"][0]
    assert story["title"] == "Bare Story"
    assert story["source"] is None
    assert story["previously_covered"] is False
    assert story["update_note"] is None
    assert story["historical_callback"] is False
    assert story["historical_note"] is None
    assert story["hn_url"] is None
    assert story["body"] == "Just a body."


# ---------------------------------------------------------------------------
# Adversarial edge case: metadata values containing colons
# ---------------------------------------------------------------------------

def test_metadata_value_with_colon(tmp_path):
    """Metadata values containing colons (e.g. URLs) must be preserved intact."""
    from build import parse_script

    content = (
        "---\n"
        "date: 2026-01-01\n"
        "duration_estimate: 2 minutes\n"
        "---\n"
        "\n"
        "# Daily Briefing — Thursday, 1 January 2026\n"
        "\n"
        "Scene-setter.\n"
        "\n"
        "## Tech\n"
        "\n"
        "### Colon Story\n"
        "source: Reuters: Breaking News\n"
        "hn_url: https://news.ycombinator.com/item?id=123\n"
        "\n"
        "Body text.\n"
    )
    script = tmp_path / "script.md"
    script.write_text(content)
    result = parse_script(script)
    story = result["sections"][0]["stories"][0]
    assert story["source"] == "Reuters: Breaking News"
    assert story["hn_url"] == "https://news.ycombinator.com/item?id=123"


# ---------------------------------------------------------------------------
# Metadata lines after blank lines must still be extracted
# ---------------------------------------------------------------------------

def test_metadata_after_blank_line_extracted(tmp_path):
    """Metadata fields appearing after a blank line (e.g. when the AI
    separates source from previously_covered) must still be parsed as
    metadata, not included in the body."""
    from build import parse_script

    content = (
        "---\n"
        "date: 2026-03-20\n"
        "duration_estimate: 5 minutes\n"
        "---\n"
        "\n"
        "# Daily Briefing — Friday, 20 March 2026\n"
        "\n"
        "Scene-setter.\n"
        "\n"
        "## World News\n"
        "\n"
        "### Some Story\n"
        "source: BBC News\n"
        "\n"
        "previously_covered: true\n"
        "update_note: What changed since last coverage\n"
        "\n"
        "The actual body text of the story.\n"
    )
    script = tmp_path / "script.md"
    script.write_text(content)
    result = parse_script(script)
    story = result["sections"][0]["stories"][0]

    assert story["previously_covered"] is True
    assert story["update_note"] == "What changed since last coverage"
    assert "previously_covered" not in story["body"]
    assert "update_note" not in story["body"]
    assert "The actual body text" in story["body"]


def test_hn_url_after_body_extracted(tmp_path):
    """hn_url placed after the body text must still be extracted as
    metadata, not included in the body."""
    from build import parse_script

    content = (
        "---\n"
        "date: 2026-03-20\n"
        "duration_estimate: 5 minutes\n"
        "---\n"
        "\n"
        "# Daily Briefing — Friday, 20 March 2026\n"
        "\n"
        "Scene-setter.\n"
        "\n"
        "## Tech\n"
        "\n"
        "### HN Story\n"
        "source: Hacker News\n"
        "\n"
        "The body text of the story.\n"
        "\n"
        "hn_url: https://news.ycombinator.com/item?id=12345\n"
    )
    script = tmp_path / "script.md"
    script.write_text(content)
    result = parse_script(script)
    story = result["sections"][0]["stories"][0]

    assert story["hn_url"] == "https://news.ycombinator.com/item?id=12345"
    assert "hn_url" not in story["body"]
    assert "The body text" in story["body"]
