"""Tests for error handling and logging: setup_logging, run_build.

These functions do not yet exist in build.py, so every test should fail on
import or because the behaviour is not implemented.

Spec: ticket day-3075 — Error handling + logging.
"""

import logging
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the project root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _clean_logging_handlers():
    """Remove handlers added to the 'build' logger between tests."""
    logger = logging.getLogger("build")
    yield
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# 1–2. Import sanity
# ---------------------------------------------------------------------------


def test_setup_logging_importable():
    """setup_logging should be importable from build."""
    from build import setup_logging

    assert callable(setup_logging)


def test_run_build_importable():
    """run_build should be importable from build."""
    from build import run_build

    assert callable(run_build)


# ---------------------------------------------------------------------------
# 3–4. setup_logging behaviour
# ---------------------------------------------------------------------------


def test_setup_logging_creates_log_file(tmp_path):
    """Logging via the configured logger should create the log file."""
    from build import setup_logging

    log_file = tmp_path / "error.log"
    setup_logging(log_file)

    # Emit a message through the module-level logger so the handler fires.
    logger = logging.getLogger("build")
    logger.error("test entry")

    assert log_file.exists(), "Log file should be created after first entry"
    content = log_file.read_text()
    assert "test entry" in content


def test_setup_logging_entries_contain_timestamps(tmp_path):
    """Each log entry should include an ISO-ish timestamp."""
    from build import setup_logging

    log_file = tmp_path / "error.log"
    setup_logging(log_file)

    logger = logging.getLogger("build")
    logger.error("timestamped message")

    content = log_file.read_text()
    # Expect something like 2026-03-19 or 2026-03-19T… at the start of a line.
    assert re.search(r"\d{4}-\d{2}-\d{2}", content), (
        "Log entry should contain a date-like timestamp"
    )


# ---------------------------------------------------------------------------
# 5–9. run_build orchestration
# ---------------------------------------------------------------------------

_PIPELINE_PATCHES = {
    "parse_script": "build.parse_script",
    "insert_stories": "build.insert_stories",
    "generate_audio": "build.generate_audio",
    "render_episode_page": "build.render_episode_page",
    "write_chapters": "build.write_chapters",
    "copy_latest_episode": "build.copy_latest_episode",
    "publish": "build.publish",
    "init_db": "build.init_db",
    "prepare_tts_text": "build.prepare_tts_text",
    "extract_chapters": "build.extract_chapters",
}


def _make_mocks():
    """Return a dict of MagicMock objects keyed by function name."""
    mocks = {}
    for name in _PIPELINE_PATCHES:
        mocks[name] = MagicMock(name=name)

    # Default return values so the happy path works.
    mocks["parse_script"].return_value = {
        "date": "2026-03-19",
        "duration_estimate": "8 min",
        "intro": "Hello",
        "sections": [],
    }
    mocks["init_db"].return_value = MagicMock()  # fake connection
    mocks["prepare_tts_text"].return_value = "Hello"
    mocks["generate_audio"] = AsyncMock(
        name="generate_audio",
        return_value=[{"text": "Hello", "offset": 0.0}],
    )
    mocks["extract_chapters"].return_value = []
    mocks["render_episode_page"].return_value = "<html></html>"
    return mocks


def _patch_all(mocks):
    """Return a contextmanager-style list of patch objects for all pipeline fns."""
    patchers = []
    for name, target in _PIPELINE_PATCHES.items():
        patchers.append(patch(target, mocks[name]))
    return patchers


def _apply_patches(patchers):
    for p in patchers:
        p.start()


def _stop_patches(patchers):
    for p in patchers:
        p.stop()


def test_run_build_calls_publish_on_success(tmp_path):
    """When every step succeeds, run_build should call publish."""
    from build import run_build

    mocks = _make_mocks()
    patchers = _patch_all(mocks)
    _apply_patches(patchers)
    try:
        run_build(
            episode_dir=str(tmp_path),
            db_path=str(tmp_path / "briefings.db"),
            docs_dir=str(tmp_path / "docs"),
        )
        mocks["publish"].assert_called_once()
    finally:
        _stop_patches(patchers)


def test_run_build_no_publish_on_parse_failure(tmp_path):
    """If parse_script raises, publish must NOT be called."""
    from build import run_build

    mocks = _make_mocks()
    mocks["parse_script"].side_effect = ValueError("bad frontmatter")
    patchers = _patch_all(mocks)
    _apply_patches(patchers)
    try:
        # run_build may raise or swallow — either way publish must not fire.
        try:
            run_build(
                episode_dir=str(tmp_path),
                db_path=str(tmp_path / "briefings.db"),
                docs_dir=str(tmp_path / "docs"),
            )
        except Exception:
            pass
        mocks["publish"].assert_not_called()
    finally:
        _stop_patches(patchers)


def test_run_build_logs_error_on_parse_failure(tmp_path):
    """If parse_script raises, the error should be logged."""
    from build import run_build, setup_logging

    log_file = tmp_path / "error.log"
    setup_logging(log_file)

    mocks = _make_mocks()
    mocks["parse_script"].side_effect = ValueError("bad frontmatter")
    patchers = _patch_all(mocks)
    _apply_patches(patchers)
    try:
        try:
            run_build(
                episode_dir=str(tmp_path),
                db_path=str(tmp_path / "briefings.db"),
                docs_dir=str(tmp_path / "docs"),
            )
        except Exception:
            pass
        content = log_file.read_text()
        assert "bad frontmatter" in content.lower() or "error" in content.lower(), (
            "Log file should contain an error entry about the parse failure"
        )
    finally:
        _stop_patches(patchers)


def test_run_build_no_publish_on_audio_failure(tmp_path):
    """If generate_audio raises, publish must NOT be called."""
    from build import run_build

    mocks = _make_mocks()
    mocks["generate_audio"].side_effect = RuntimeError("TTS failed")
    patchers = _patch_all(mocks)
    _apply_patches(patchers)
    try:
        try:
            run_build(
                episode_dir=str(tmp_path),
                db_path=str(tmp_path / "briefings.db"),
                docs_dir=str(tmp_path / "docs"),
            )
        except Exception:
            pass
        mocks["publish"].assert_not_called()
    finally:
        _stop_patches(patchers)


def test_run_build_logs_error_on_audio_failure(tmp_path):
    """If generate_audio raises, the error should be logged."""
    from build import run_build, setup_logging

    log_file = tmp_path / "error.log"
    setup_logging(log_file)

    mocks = _make_mocks()
    mocks["generate_audio"].side_effect = RuntimeError("TTS failed")
    patchers = _patch_all(mocks)
    _apply_patches(patchers)
    try:
        try:
            run_build(
                episode_dir=str(tmp_path),
                db_path=str(tmp_path / "briefings.db"),
                docs_dir=str(tmp_path / "docs"),
            )
        except Exception:
            pass
        content = log_file.read_text()
        assert "tts failed" in content.lower() or "error" in content.lower(), (
            "Log file should contain an error entry about the audio failure"
        )
    finally:
        _stop_patches(patchers)


# ---------------------------------------------------------------------------
# 10–11. insert_stories failure → warning, pipeline continues
# ---------------------------------------------------------------------------


def test_run_build_continues_after_insert_stories_failure(tmp_path):
    """If insert_stories raises, run_build should still call generate_audio."""
    from build import run_build

    mocks = _make_mocks()
    mocks["insert_stories"].side_effect = Exception("DB insert failed")
    patchers = _patch_all(mocks)
    _apply_patches(patchers)
    try:
        run_build(
            episode_dir=str(tmp_path),
            db_path=str(tmp_path / "briefings.db"),
            docs_dir=str(tmp_path / "docs"),
        )
        mocks["generate_audio"].assert_called_once()
    finally:
        _stop_patches(patchers)


def test_run_build_logs_warning_on_insert_stories_failure(tmp_path):
    """If insert_stories raises, a warning should be logged."""
    from build import run_build, setup_logging

    log_file = tmp_path / "error.log"
    setup_logging(log_file)

    mocks = _make_mocks()
    mocks["insert_stories"].side_effect = Exception("DB insert failed")
    patchers = _patch_all(mocks)
    _apply_patches(patchers)
    try:
        run_build(
            episode_dir=str(tmp_path),
            db_path=str(tmp_path / "briefings.db"),
            docs_dir=str(tmp_path / "docs"),
        )
        content = log_file.read_text()
        assert "db insert failed" in content.lower() or "warning" in content.lower(), (
            "Log file should contain a warning about the DB failure"
        )
    finally:
        _stop_patches(patchers)
