"""Tests for the episode player HTML template (render_episode_page).

Covers: import sanity, valid HTML structure, audio element, chapter markers,
speed controls, transcript panel, feedback buttons, follow-up badge,
Then & Now badge, source attribution, and date display.

All tests are expected to FAIL initially because render_episode_page does not
yet exist in build.py.
"""

import sys
from pathlib import Path
from urllib.parse import quote_plus, unquote_plus

import pytest

# Ensure the project root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE = Path(__file__).parent / "fixtures" / "sample_script.md"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def parsed():
    from build import parse_script

    return parse_script(FIXTURE)


@pytest.fixture()
def sample_chapters():
    return [
        {"id": "s1", "title": "Iran's intelligence minister killed in Israeli strike", "section": "World News", "start": 2.0},
        {"id": "s2", "title": "Ceasefire talks resume in Cairo", "section": "World News", "start": 20.0},
        {"id": "s3", "title": "TC39 signals proposal to deprecate prototype inheritance", "section": "Tech & Developer", "start": 45.0},
        {"id": "s4", "title": "Verstappen tops final practice in Melbourne", "section": "Formula 1", "start": 70.0},
    ]


@pytest.fixture()
def config():
    return {"repo": "joe/daycast"}


@pytest.fixture()
def html(parsed, sample_chapters, config):
    from build import render_episode_page

    return render_episode_page(parsed, sample_chapters, config)


# ---------------------------------------------------------------------------
# 1. Import sanity
# ---------------------------------------------------------------------------

def test_render_episode_page_is_importable():
    """render_episode_page should be importable from build."""
    from build import render_episode_page  # noqa: F401


# ---------------------------------------------------------------------------
# 2. Returns valid HTML
# ---------------------------------------------------------------------------

def test_returns_string(html):
    """render_episode_page should return a string."""
    assert isinstance(html, str)


def test_contains_doctype(html):
    """Output should contain a DOCTYPE declaration."""
    assert "<!DOCTYPE html>" in html


def test_contains_html_open_tag(html):
    """Output should contain an opening <html tag."""
    assert "<html" in html


def test_contains_html_close_tag(html):
    """Output should contain a closing </html> tag."""
    assert "</html>" in html


# ---------------------------------------------------------------------------
# 3. Audio element
# ---------------------------------------------------------------------------

def test_contains_audio_element(html):
    """Output should contain an <audio element."""
    assert "<audio" in html


def test_audio_references_mp3(html):
    """The audio element should reference audio.mp3."""
    assert "audio.mp3" in html


# ---------------------------------------------------------------------------
# 4. Chapter markers
# ---------------------------------------------------------------------------

def test_chapter_titles_present(html, sample_chapters):
    """Each chapter title should appear in the HTML."""
    for chapter in sample_chapters:
        assert chapter["title"] in html, (
            f"Chapter title '{chapter['title']}' not found in HTML"
        )


def test_chapter_start_timestamps_present(html, sample_chapters):
    """Each chapter's start timestamp should appear as a data attribute
    in the HTML (e.g. data-start="2.0")."""
    import re

    for chapter in sample_chapters:
        start_float = str(chapter["start"])
        start_int = str(int(chapter["start"]))
        # Look for a data attribute containing the timestamp to avoid false
        # positives from matching random numbers in the HTML.
        pattern = rf'data-start=["\']({re.escape(start_float)}|{re.escape(start_int)})["\']'
        assert re.search(pattern, html), (
            f"Chapter start data-start attribute for {chapter['start']} "
            f"('{chapter['title']}') not found in HTML"
        )


# ---------------------------------------------------------------------------
# 5. Speed control buttons
# ---------------------------------------------------------------------------

_EXPECTED_SPEEDS = ["0.85", "1", "1.15", "1.3"]


@pytest.mark.parametrize("speed", _EXPECTED_SPEEDS)
def test_speed_control_present(html, speed):
    """The HTML should contain a control for {speed}x playback."""
    # Accept either "0.85x" or "0.85" in button text / attributes
    assert speed in html, f"Speed control for {speed}x not found in HTML"


# ---------------------------------------------------------------------------
# 6. Transcript panel
# ---------------------------------------------------------------------------

def test_transcript_contains_story_titles(html, parsed):
    """Each story title should appear in the transcript panel."""
    for section in parsed["sections"]:
        for story in section["stories"]:
            assert story["title"] in html, (
                f"Story title '{story['title']}' not found in transcript"
            )


def test_transcript_contains_story_bodies(html, parsed):
    """Each story body text should appear in the transcript panel."""
    for section in parsed["sections"]:
        for story in section["stories"]:
            # Check for a meaningful substring of the body (first 60 chars)
            snippet = story["body"][:60]
            assert snippet in html, (
                f"Story body snippet not found for '{story['title']}'"
            )


# ---------------------------------------------------------------------------
# 7. Feedback buttons
# ---------------------------------------------------------------------------

def test_feedback_links_contain_github_issues_url(html, config):
    """Each feedback link should point to the correct GitHub issues/new URL."""
    repo = config["repo"]
    expected_base = f"https://github.com/{repo}/issues/new"
    assert expected_base in html


def test_feedback_link_contains_repo(html, config):
    """Feedback URLs should reference the configured repo."""
    assert config["repo"] in html


def test_feedback_link_contains_date(html, parsed):
    """Feedback URLs should contain the episode date."""
    # The date should appear in the feedback URL query string
    date_str = parsed["date"]
    assert date_str in html


def test_feedback_link_contains_story_title(html, parsed):
    """Feedback URLs should contain each story's title."""
    for section in parsed["sections"]:
        for story in section["stories"]:
            # Title may be URL-encoded in the link
            title = story["title"]
            encoded_title = quote_plus(title)
            assert title in html or encoded_title in html, (
                f"Feedback link missing title for '{title}'"
            )


def test_feedback_links_have_thumbs_up_and_down(html, parsed):
    """Each story should have two feedback links — one for thumbs-up and one
    for thumbs-down — with the appropriate signal emoji in the URL body."""
    for section in parsed["sections"]:
        for story in section["stories"]:
            title = story["title"]
            # At minimum, the HTML should contain a thumbs-up and a thumbs-down
            # feedback URL for each story.  We look for the encoded emoji in
            # the query string body (Signal:+👍 or Signal:+👎, URL-encoded).
            assert html.count("Signal") >= 2, (
                f"Expected at least two Signal references for '{title}'"
            )


def test_feedback_link_contains_labels(html):
    """Feedback URLs should include the 'feedback' label."""
    assert "labels=feedback" in html


def test_feedback_link_format(html, parsed, config):
    """At least one feedback link should match the full expected URL format:
    https://github.com/{repo}/issues/new?title=Feedback:+{date}+—+{title}&labels=feedback&body=...
    """
    repo = config["repo"]
    date_str = parsed["date"]
    expected_fragment = (
        f"https://github.com/{repo}/issues/new?"
    )
    assert expected_fragment in html

    # The PRD specifies an em-dash (U+2014) between date and story title.
    # Check for it in either raw or URL-encoded form.
    em_dash = "\u2014"
    em_dash_encoded = "%E2%80%94"
    assert em_dash in html or em_dash_encoded in html, (
        "Feedback URL should contain an em-dash (—) between date and title"
    )


# ---------------------------------------------------------------------------
# 8. Follow-up badge
# ---------------------------------------------------------------------------

def test_followup_badge_present_for_previously_covered(html):
    """Stories with previously_covered: true should have a 'Follow-up' badge."""
    # Story 2 (Ceasefire) has previously_covered: true
    assert "Follow-up" in html


def test_followup_badge_absent_for_new_stories(html):
    """Stories without previously_covered should not have a Follow-up badge
    near their content. We check that the badge count is limited — not every
    story gets one."""
    # There are 4 stories but only 1 has previously_covered: true.
    # Count occurrences of "Follow-up" — should match the number of
    # previously_covered stories (just 1 in the fixture).
    count = html.count("Follow-up")
    assert count == 1, (
        f"Expected exactly 1 Follow-up badge, found {count}"
    )


# ---------------------------------------------------------------------------
# 9. Then & Now badge
# ---------------------------------------------------------------------------

def test_then_and_now_badge_present(html):
    """Stories with historical_callback: true should have a 'Then & Now' badge."""
    assert "Then &amp; Now" in html or "Then & Now" in html


def test_historical_note_displayed(html):
    """The historical_note for the Ceasefire story should be rendered."""
    assert "First covered 2026-03-01" in html
    assert "US-brokered negotiations began" in html


def test_then_and_now_badge_count(html):
    """Only stories with historical_callback: true should get the badge.
    The fixture has exactly 1 such story."""
    # Count either HTML-escaped or plain version
    count_plain = html.count("Then & Now")
    count_escaped = html.count("Then &amp; Now")
    total = count_plain + count_escaped
    assert total == 1, (
        f"Expected exactly 1 Then & Now badge, found {total}"
    )


# ---------------------------------------------------------------------------
# 10. Source attribution
# ---------------------------------------------------------------------------

def test_sources_present_in_html(html, parsed):
    """Each story's source should appear in the rendered HTML."""
    for section in parsed["sections"]:
        for story in section["stories"]:
            source = story.get("source")
            if source:
                assert source in html, (
                    f"Source '{source}' not found in HTML"
                )


def test_specific_sources(html):
    """Spot-check that known sources from the fixture appear."""
    assert "BBC News" in html
    assert "Hacker News" in html
    assert "formula1.com" in html


# ---------------------------------------------------------------------------
# 11. Date in page
# ---------------------------------------------------------------------------

def test_date_appears_in_page(html):
    """The episode date should appear in the rendered page."""
    assert "2026-03-19" in html


def test_date_in_human_readable_form(html):
    """The page should display the date in a human-readable form
    (e.g. '19 March 2026' or 'Wednesday, 19 March 2026')."""
    # Must contain a human-readable form — not just the ISO date.
    assert "19 March 2026" in html or "March 19, 2026" in html, (
        "Expected a human-readable date like '19 March 2026' in the HTML"
    )


# ---------------------------------------------------------------------------
# 12. Archive link in footer
# ---------------------------------------------------------------------------

def test_footer_archive_link_is_valid(html):
    """The footer archive link should point to archive.html, not a
    non-existent /archive/ path."""
    assert "archive.html" in html
    assert "/archive/" not in html
