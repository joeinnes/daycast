"""Tests for publishing functions: copy_latest_episode, render_archive, publish.

These functions do not yet exist in build.py, so every test should fail on
import or because the behaviour is not implemented.

Spec: ticket day-1595 — GitHub Pages publishing + archive page.
"""

import sys
from pathlib import Path
from unittest.mock import patch, call

import pytest

# Ensure the project root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helpers: minimal script.md content for archive tests
# ---------------------------------------------------------------------------

def _minimal_script(date: str, duration: str) -> str:
    """Return a minimal but parseable script.md string."""
    return (
        f"---\n"
        f"date: {date}\n"
        f"duration_estimate: {duration}\n"
        f"---\n"
        f"\n"
        f"# Daily Briefing — {date}\n"
        f"\n"
        f"Scene-setter paragraph.\n"
        f"\n"
        f"## Section One\n"
        f"\n"
        f"### A Story\n"
        f"source: Test Source\n"
        f"\n"
        f"Body text for the story.\n"
    )


# ===========================================================================
# copy_latest_episode
# ===========================================================================


# ---------------------------------------------------------------------------
# 1. Import sanity
# ---------------------------------------------------------------------------

def test_copy_latest_episode_is_importable():
    """copy_latest_episode should be importable from build."""
    from build import copy_latest_episode  # noqa: F401


# ---------------------------------------------------------------------------
# 2. Copies index.html correctly
# ---------------------------------------------------------------------------

def test_copy_latest_episode_copies_file(tmp_path):
    """index.html from episode_dir should appear in docs_dir."""
    from build import copy_latest_episode

    episode_dir = tmp_path / "episode"
    episode_dir.mkdir()
    (episode_dir / "index.html").write_text("<html>hello</html>")

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    copy_latest_episode(episode_dir, docs_dir)

    result = (docs_dir / "index.html").read_text()
    assert result == "<html>hello</html>"


# ---------------------------------------------------------------------------
# 3. Creates docs_dir if missing
# ---------------------------------------------------------------------------

def test_copy_latest_episode_creates_docs_dir(tmp_path):
    """docs_dir should be created if it does not exist."""
    from build import copy_latest_episode

    episode_dir = tmp_path / "episode"
    episode_dir.mkdir()
    (episode_dir / "index.html").write_text("<html>created</html>")

    docs_dir = tmp_path / "docs"
    assert not docs_dir.exists()

    copy_latest_episode(episode_dir, docs_dir)

    assert docs_dir.exists()
    assert (docs_dir / "index.html").read_text() == "<html>created</html>"


# ---------------------------------------------------------------------------
# 4. Overwrites existing file
# ---------------------------------------------------------------------------

def test_copy_latest_episode_overwrites_existing(tmp_path):
    """An existing docs/index.html should be overwritten."""
    from build import copy_latest_episode

    episode_dir = tmp_path / "episode"
    episode_dir.mkdir()
    (episode_dir / "index.html").write_text("<html>new</html>")

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "index.html").write_text("<html>old</html>")

    copy_latest_episode(episode_dir, docs_dir)

    assert (docs_dir / "index.html").read_text() == "<html>new</html>"


# ===========================================================================
# render_archive
# ===========================================================================


# ---------------------------------------------------------------------------
# 5. Import sanity
# ---------------------------------------------------------------------------

def test_render_archive_is_importable():
    """render_archive should be importable from build."""
    from build import render_archive  # noqa: F401


# ---------------------------------------------------------------------------
# 6. Returns valid HTML string
# ---------------------------------------------------------------------------

def test_render_archive_returns_html(tmp_path):
    """render_archive should return a string containing basic HTML structure."""
    from build import render_archive

    ep = tmp_path / "2026-03-19"
    ep.mkdir()
    (ep / "script.md").write_text(_minimal_script("2026-03-19", "7 minutes"))

    html = render_archive(tmp_path)
    assert isinstance(html, str)
    assert "<html" in html.lower()
    assert "</html>" in html.lower()


# ---------------------------------------------------------------------------
# 7. Lists episodes newest-first
# ---------------------------------------------------------------------------

def test_render_archive_newest_first(tmp_path):
    """Episodes should be listed with the newest date first."""
    from build import render_archive

    for date_str in ("2026-03-17", "2026-03-19", "2026-03-18"):
        ep = tmp_path / date_str
        ep.mkdir()
        (ep / "script.md").write_text(
            _minimal_script(date_str, "5 minutes")
        )

    html = render_archive(tmp_path)
    pos_19 = html.index("2026-03-19")
    pos_18 = html.index("2026-03-18")
    pos_17 = html.index("2026-03-17")
    assert pos_19 < pos_18 < pos_17


# ---------------------------------------------------------------------------
# 8. Contains date for each episode
# ---------------------------------------------------------------------------

def test_render_archive_contains_dates(tmp_path):
    """Each episode's date should appear in the archive HTML."""
    from build import render_archive

    for date_str in ("2026-03-17", "2026-03-19"):
        ep = tmp_path / date_str
        ep.mkdir()
        (ep / "script.md").write_text(
            _minimal_script(date_str, "5 minutes")
        )

    html = render_archive(tmp_path)
    assert "2026-03-17" in html
    assert "2026-03-19" in html


# ---------------------------------------------------------------------------
# 9. Contains duration estimate for each episode
# ---------------------------------------------------------------------------

def test_render_archive_contains_duration(tmp_path):
    """Each episode's duration_estimate should appear in the archive HTML."""
    from build import render_archive

    ep = tmp_path / "2026-03-19"
    ep.mkdir()
    (ep / "script.md").write_text(_minimal_script("2026-03-19", "7 minutes"))

    html = render_archive(tmp_path)
    assert "7 minutes" in html


# ---------------------------------------------------------------------------
# 10. Contains links to episode pages
# ---------------------------------------------------------------------------

def test_render_archive_contains_links(tmp_path):
    """Each episode entry should link to its player page."""
    from build import render_archive

    ep = tmp_path / "2026-03-19"
    ep.mkdir()
    (ep / "script.md").write_text(_minimal_script("2026-03-19", "7 minutes"))

    html = render_archive(tmp_path)
    # The date should appear inside an href attribute to count as a link.
    import re
    assert re.search(r'href="[^"]*2026-03-19[^"]*"', html), (
        "Expected an <a> tag with href containing '2026-03-19'"
    )


# ---------------------------------------------------------------------------
# 11. Handles empty episodes directory
# ---------------------------------------------------------------------------

def test_render_archive_empty_dir(tmp_path):
    """An empty episodes directory should produce valid HTML with no entries."""
    from build import render_archive

    html = render_archive(tmp_path)
    assert isinstance(html, str)
    assert "<html" in html.lower()


# ===========================================================================
# publish
# ===========================================================================


# ---------------------------------------------------------------------------
# 12. Import sanity
# ---------------------------------------------------------------------------

def test_publish_is_importable():
    """publish should be importable from build."""
    from build import publish  # noqa: F401


# ---------------------------------------------------------------------------
# 13. Calls git add
# ---------------------------------------------------------------------------

def test_publish_calls_git_add(tmp_path):
    """publish should run git add on the docs directory."""
    from build import publish

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        publish(tmp_path)

    commands = [c.args[0] for c in mock_run.call_args_list]
    git_add_calls = [c for c in commands if "git" in str(c) and "add" in str(c)]
    assert len(git_add_calls) >= 1


# ---------------------------------------------------------------------------
# 14. Calls git commit
# ---------------------------------------------------------------------------

def test_publish_calls_git_commit(tmp_path):
    """publish should run git commit."""
    from build import publish

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        publish(tmp_path)

    commands = [c.args[0] for c in mock_run.call_args_list]
    git_commit_calls = [c for c in commands if "git" in str(c) and "commit" in str(c)]
    assert len(git_commit_calls) >= 1


# ---------------------------------------------------------------------------
# 15. Calls git push
# ---------------------------------------------------------------------------

def test_publish_calls_git_push(tmp_path):
    """publish should run git push."""
    from build import publish

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        publish(tmp_path)

    commands = [c.args[0] for c in mock_run.call_args_list]
    git_push_calls = [c for c in commands if "git" in str(c) and "push" in str(c)]
    assert len(git_push_calls) >= 1


# ---------------------------------------------------------------------------
# 16. On push failure: does not retry, does not raise
# ---------------------------------------------------------------------------

def test_publish_no_retry_on_push_failure(tmp_path):
    """When git push fails, publish should NOT retry the push.

    Per PRD S12: no retry on push failure.
    """
    from build import publish

    def side_effect(cmd, *args, **kwargs):
        """Simulate push failure while add/commit succeed."""
        from unittest.mock import MagicMock
        result = MagicMock()
        if "push" in str(cmd):
            result.returncode = 1
            result.stderr = "remote: error"
        else:
            result.returncode = 0
        return result

    with patch("subprocess.run", side_effect=side_effect) as mock_run:
        # Should not raise even though push failed.
        publish(tmp_path)

    commands = [c.args[0] for c in mock_run.call_args_list]
    git_push_calls = [c for c in commands if "git" in str(c) and "push" in str(c)]
    assert len(git_push_calls) == 1, "push should be attempted exactly once (no retry)"


# ---------------------------------------------------------------------------
# 17. On push failure: logs the error
# ---------------------------------------------------------------------------

def test_publish_logs_error_on_push_failure(tmp_path):
    """When git push fails, publish should log the error."""
    from build import publish
    import logging

    def side_effect(cmd, *args, **kwargs):
        from unittest.mock import MagicMock
        result = MagicMock()
        if "push" in str(cmd):
            result.returncode = 1
            result.stderr = "remote: error"
        else:
            result.returncode = 0
        return result

    with patch("subprocess.run", side_effect=side_effect):
        with patch("build._log") as mock_log:
            publish(tmp_path)

    # Should have logged at error or warning level.
    assert mock_log.error.called or mock_log.warning.called, (
        "publish should log when push fails"
    )


# ---------------------------------------------------------------------------
# 18. Skips git when audio_ok is False
# ---------------------------------------------------------------------------

def test_publish_skips_git_when_audio_failed(tmp_path):
    """When audio generation did not succeed, publish should not run any
    git commands.  The caller signals this via ``audio_ok=False``."""
    from build import publish

    with patch("subprocess.run") as mock_run:
        publish(tmp_path, audio_ok=False)

    mock_run.assert_not_called()
