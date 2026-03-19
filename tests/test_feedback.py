"""Tests for the feedback loop: GitHub issues to interests.md.

Covers parse_feedback_issue, fetch_feedback_issues, close_issue,
update_interests, and process_feedback from build.py.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Ensure the project root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_ISSUE_BODY_THUMBS_UP = (
    "Date: 2026-03-19\n"
    "Story: Iran's intelligence minister killed in Israeli strike\n"
    "Signal: \U0001F44D\n"
    "Note: "
)

SAMPLE_ISSUE_BODY_THUMBS_DOWN = (
    "Date: 2026-03-18\n"
    "Story: EU proposes new AI regulation framework\n"
    "Signal: \U0001F44E\n"
    "Note: Too much speculation, not enough facts"
)

SAMPLE_ISSUE_THUMBS_UP = {
    "title": "Feedback: 2026-03-19 \u2014 Iran's intelligence minister killed in Israeli strike",
    "body": SAMPLE_ISSUE_BODY_THUMBS_UP,
    "labels": [{"name": "feedback"}],
    "number": 42,
    "html_url": "https://github.com/user/repo/issues/42",
}

SAMPLE_ISSUE_THUMBS_DOWN = {
    "title": "Feedback: 2026-03-18 \u2014 EU proposes new AI regulation framework",
    "body": SAMPLE_ISSUE_BODY_THUMBS_DOWN,
    "labels": [{"name": "feedback"}],
    "number": 43,
    "html_url": "https://github.com/user/repo/issues/43",
}

SAMPLE_INTERESTS_MD = """\
# Listener Interests

## Always Include
- UK news

## Strong Interest
- Technology
- Middle East

## Explicit Feedback Notes
- 2026-03-17: \U0001F44D Some older story
"""

SAMPLE_INTERESTS_MD_NO_SECTION = """\
# Listener Interests

## Always Include
- UK news

## Strong Interest
- Technology
"""


# ---------------------------------------------------------------------------
# parse_feedback_issue
# ---------------------------------------------------------------------------

class TestParseFeedbackIssueImport:
    def test_importable(self):
        """parse_feedback_issue should be importable from build."""
        from build import parse_feedback_issue  # noqa: F401


class TestParseFeedbackIssue:
    def test_extracts_date(self):
        from build import parse_feedback_issue

        result = parse_feedback_issue(SAMPLE_ISSUE_THUMBS_UP)
        assert result["date"] == "2026-03-19"

    def test_extracts_story_title(self):
        from build import parse_feedback_issue

        result = parse_feedback_issue(SAMPLE_ISSUE_THUMBS_UP)
        assert result["story_title"] == "Iran's intelligence minister killed in Israeli strike"

    def test_extracts_signal_thumbs_up(self):
        from build import parse_feedback_issue

        result = parse_feedback_issue(SAMPLE_ISSUE_THUMBS_UP)
        assert result["signal"] == "\U0001F44D"

    def test_extracts_signal_thumbs_down(self):
        from build import parse_feedback_issue

        result = parse_feedback_issue(SAMPLE_ISSUE_THUMBS_DOWN)
        assert result["signal"] == "\U0001F44E"

    def test_handles_empty_note(self):
        from build import parse_feedback_issue

        result = parse_feedback_issue(SAMPLE_ISSUE_THUMBS_UP)
        assert result["note"] == ""

    def test_handles_note_with_text(self):
        from build import parse_feedback_issue

        result = parse_feedback_issue(SAMPLE_ISSUE_THUMBS_DOWN)
        assert result["note"] == "Too much speculation, not enough facts"


# ---------------------------------------------------------------------------
# fetch_feedback_issues
# ---------------------------------------------------------------------------

class TestFetchFeedbackIssuesImport:
    def test_importable(self):
        """fetch_feedback_issues should be importable from build."""
        from build import fetch_feedback_issues  # noqa: F401


class TestFetchFeedbackIssues:
    @patch("urllib.request.urlopen")
    def test_returns_parsed_json(self, mock_urlopen):
        from build import fetch_feedback_issues

        issues_payload = [SAMPLE_ISSUE_THUMBS_UP, SAMPLE_ISSUE_THUMBS_DOWN]
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(issues_payload).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = fetch_feedback_issues("user/repo", "fake-token")
        assert len(result) == 2
        assert result[0]["number"] == 42

    @patch("urllib.request.urlopen")
    def test_sends_authorization_header(self, mock_urlopen):
        from build import fetch_feedback_issues

        mock_response = MagicMock()
        mock_response.read.return_value = b"[]"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        fetch_feedback_issues("user/repo", "fake-token")

        # The Request object passed to urlopen should have the auth header.
        request_obj = mock_urlopen.call_args[0][0]
        assert request_obj.get_header("Authorization") == "token fake-token"


# ---------------------------------------------------------------------------
# close_issue
# ---------------------------------------------------------------------------

class TestCloseIssueImport:
    def test_importable(self):
        """close_issue should be importable from build."""
        from build import close_issue  # noqa: F401


class TestCloseIssue:
    @patch("urllib.request.urlopen")
    def test_sends_patch_with_state_closed(self, mock_urlopen):
        from build import close_issue

        mock_response = MagicMock()
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        close_issue("user/repo", "fake-token", 42)

        request_obj = mock_urlopen.call_args[0][0]
        assert request_obj.full_url == "https://api.github.com/repos/user/repo/issues/42"
        assert request_obj.method == "PATCH"
        body = json.loads(request_obj.data)
        assert body == {"state": "closed"}


# ---------------------------------------------------------------------------
# update_interests
# ---------------------------------------------------------------------------

class TestUpdateInterestsImport:
    def test_importable(self):
        """update_interests should be importable from build."""
        from build import update_interests  # noqa: F401


class TestUpdateInterests:
    def test_appends_entry_to_section(self, tmp_path):
        from build import update_interests

        interests_file = tmp_path / "interests.md"
        interests_file.write_text(SAMPLE_INTERESTS_MD)

        feedbacks = [
            {
                "date": "2026-03-19",
                "story_title": "Iran's intelligence minister killed in Israeli strike",
                "signal": "\U0001F44D",
                "note": "",
            },
        ]
        update_interests(interests_file, feedbacks)

        content = interests_file.read_text()
        assert "- 2026-03-19: \U0001F44D Iran's intelligence minister killed in Israeli strike" in content

    def test_preserves_other_sections(self, tmp_path):
        from build import update_interests

        interests_file = tmp_path / "interests.md"
        interests_file.write_text(SAMPLE_INTERESTS_MD)

        feedbacks = [
            {
                "date": "2026-03-19",
                "story_title": "Test story",
                "signal": "\U0001F44D",
                "note": "",
            },
        ]
        update_interests(interests_file, feedbacks)

        content = interests_file.read_text()
        assert "## Always Include" in content
        assert "- UK news" in content
        assert "## Strong Interest" in content
        assert "- Technology" in content
        assert "- Middle East" in content

    def test_handles_thumbs_up_emoji(self, tmp_path):
        from build import update_interests

        interests_file = tmp_path / "interests.md"
        interests_file.write_text(SAMPLE_INTERESTS_MD)

        feedbacks = [
            {
                "date": "2026-03-19",
                "story_title": "Good story",
                "signal": "\U0001F44D",
                "note": "",
            },
        ]
        update_interests(interests_file, feedbacks)

        content = interests_file.read_text()
        assert "- 2026-03-19: \U0001F44D Good story" in content

    def test_handles_thumbs_down_emoji(self, tmp_path):
        from build import update_interests

        interests_file = tmp_path / "interests.md"
        interests_file.write_text(SAMPLE_INTERESTS_MD)

        feedbacks = [
            {
                "date": "2026-03-18",
                "story_title": "Bad story",
                "signal": "\U0001F44E",
                "note": "",
            },
        ]
        update_interests(interests_file, feedbacks)

        content = interests_file.read_text()
        assert "- 2026-03-18: \U0001F44E Bad story" in content

    def test_handles_note_with_text(self, tmp_path):
        from build import update_interests

        interests_file = tmp_path / "interests.md"
        interests_file.write_text(SAMPLE_INTERESTS_MD)

        feedbacks = [
            {
                "date": "2026-03-18",
                "story_title": "Some story",
                "signal": "\U0001F44E",
                "note": "Too speculative",
            },
        ]
        update_interests(interests_file, feedbacks)

        content = interests_file.read_text()
        assert "- 2026-03-18: \U0001F44E Some story (Too speculative)" in content

    def test_works_when_section_already_has_entries(self, tmp_path):
        from build import update_interests

        interests_file = tmp_path / "interests.md"
        interests_file.write_text(SAMPLE_INTERESTS_MD)

        feedbacks = [
            {
                "date": "2026-03-19",
                "story_title": "New feedback story",
                "signal": "\U0001F44D",
                "note": "",
            },
        ]
        update_interests(interests_file, feedbacks)

        content = interests_file.read_text()
        # Original entry still present.
        assert "- 2026-03-17: \U0001F44D Some older story" in content
        # New entry appended.
        assert "- 2026-03-19: \U0001F44D New feedback story" in content

    def test_creates_section_if_missing(self, tmp_path):
        from build import update_interests

        interests_file = tmp_path / "interests.md"
        interests_file.write_text(SAMPLE_INTERESTS_MD_NO_SECTION)

        feedbacks = [
            {
                "date": "2026-03-19",
                "story_title": "A story",
                "signal": "\U0001F44D",
                "note": "",
            },
        ]
        update_interests(interests_file, feedbacks)

        content = interests_file.read_text()
        assert "## Explicit Feedback Notes" in content
        assert "- 2026-03-19: \U0001F44D A story" in content


# ---------------------------------------------------------------------------
# process_feedback
# ---------------------------------------------------------------------------

class TestProcessFeedbackImport:
    def test_importable(self):
        """process_feedback should be importable from build."""
        from build import process_feedback  # noqa: F401


class TestProcessFeedback:
    @patch("build.close_issue", create=True)
    @patch("build.update_interests", create=True)
    @patch("build.parse_feedback_issue", create=True)
    @patch("build.fetch_feedback_issues", create=True)
    def test_returns_count_of_processed_issues(
        self, mock_fetch, mock_parse, mock_update, mock_close, tmp_path
    ):
        from build import process_feedback

        mock_fetch.return_value = [SAMPLE_ISSUE_THUMBS_UP, SAMPLE_ISSUE_THUMBS_DOWN]
        mock_parse.side_effect = [
            {"date": "2026-03-19", "story_title": "Story A", "signal": "\U0001F44D", "note": ""},
            {"date": "2026-03-18", "story_title": "Story B", "signal": "\U0001F44E", "note": "meh"},
        ]

        interests_file = tmp_path / "interests.md"
        interests_file.write_text(SAMPLE_INTERESTS_MD)

        count = process_feedback("user/repo", "fake-token", interests_file)
        assert count == 2

    @patch("build.close_issue", create=True)
    @patch("build.update_interests", create=True)
    @patch("build.parse_feedback_issue", create=True)
    @patch("build.fetch_feedback_issues", create=True)
    def test_closes_each_processed_issue(
        self, mock_fetch, mock_parse, mock_update, mock_close, tmp_path
    ):
        from build import process_feedback

        mock_fetch.return_value = [SAMPLE_ISSUE_THUMBS_UP, SAMPLE_ISSUE_THUMBS_DOWN]
        mock_parse.side_effect = [
            {"date": "2026-03-19", "story_title": "Story A", "signal": "\U0001F44D", "note": ""},
            {"date": "2026-03-18", "story_title": "Story B", "signal": "\U0001F44E", "note": "meh"},
        ]

        interests_file = tmp_path / "interests.md"
        interests_file.write_text(SAMPLE_INTERESTS_MD)

        process_feedback("user/repo", "fake-token", interests_file)

        assert mock_close.call_count == 2
        mock_close.assert_any_call("user/repo", "fake-token", 42)
        mock_close.assert_any_call("user/repo", "fake-token", 43)
