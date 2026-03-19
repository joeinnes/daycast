"""Tests for the SQLite database layer (build.init_db, insert_stories, etc.).

These tests validate the behaviours specified in ticket day-0e69:
- Database initialisation (schema creation, idempotency)
- Story ID generation (slugification)
- Story insertion (from parsed scripts, boolean mapping, FTS, duplicates)
- Recency query (date-windowed retrieval)
- Historical context query (FTS outside recency window, result limit)
- Rebuild (drop + replay from episode files)

All tests are expected to FAIL initially because the functions do not yet
exist in build.py.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

# Ensure the project root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE = Path(__file__).parent / "fixtures" / "sample_script.md"


# ---------------------------------------------------------------------------
# Import sanity
# ---------------------------------------------------------------------------

def test_init_db_is_importable():
    """init_db should be importable from build."""
    from build import init_db  # noqa: F401


def test_make_story_id_is_importable():
    """make_story_id should be importable from build."""
    from build import make_story_id  # noqa: F401


def test_insert_stories_is_importable():
    """insert_stories should be importable from build."""
    from build import insert_stories  # noqa: F401


def test_query_recent_is_importable():
    """query_recent should be importable from build."""
    from build import query_recent  # noqa: F401


def test_query_historical_is_importable():
    """query_historical should be importable from build."""
    from build import query_historical  # noqa: F401


def test_rebuild_db_is_importable():
    """rebuild_db should be importable from build."""
    from build import rebuild_db  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """Create a DB via init_db in a temp directory."""
    from build import init_db

    db_path = tmp_path / "briefings.db"
    return init_db(db_path)


@pytest.fixture()
def parsed():
    """Parse the sample fixture for use in insertion tests."""
    from build import parse_script

    return parse_script(FIXTURE)


def _insert_story_row(conn, *, story_id, story_date, title, section=None,
                       body="Body text.", source=None, previously_covered=0,
                       update_note=None, historical_callback=0,
                       historical_note=None, hn_url=None):
    """Insert a story row directly for query tests."""
    conn.execute(
        """INSERT INTO stories
           (id, date, title, section, body, source, previously_covered,
            update_note, historical_callback, historical_note, hn_url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (story_id, story_date, title, section, body, source,
         previously_covered, update_note, historical_callback,
         historical_note, hn_url),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1. init_db creates the stories table with correct columns
# ---------------------------------------------------------------------------

def test_init_db_creates_stories_table(db):
    """The stories table should exist after init_db."""
    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='stories'"
    )
    assert cursor.fetchone() is not None


def test_init_db_stories_table_has_correct_columns(db):
    """The stories table should have all specified columns."""
    cursor = db.execute("PRAGMA table_info(stories)")
    columns = {row[1] for row in cursor.fetchall()}
    expected = {
        "id", "date", "title", "section", "body", "source",
        "previously_covered", "update_note", "historical_callback",
        "historical_note", "hn_url",
    }
    assert expected == columns


# ---------------------------------------------------------------------------
# 2. init_db creates the FTS virtual table
# ---------------------------------------------------------------------------

def test_init_db_creates_fts_table(db):
    """The stories_fts FTS5 virtual table should exist after init_db."""
    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='stories_fts'"
    )
    assert cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# 3. init_db is idempotent
# ---------------------------------------------------------------------------

def test_init_db_idempotent(tmp_path):
    """Calling init_db twice on the same path should not raise."""
    from build import init_db

    db_path = tmp_path / "briefings.db"
    conn1 = init_db(db_path)
    conn1.close()
    conn2 = init_db(db_path)
    # Verify the table still exists
    cursor = conn2.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='stories'"
    )
    assert cursor.fetchone() is not None
    conn2.close()


# ---------------------------------------------------------------------------
# 4. make_story_id generates slugified IDs from date and title
# ---------------------------------------------------------------------------

def test_make_story_id_basic():
    """A simple title produces a lowercase hyphenated slug prefixed by date."""
    from build import make_story_id

    result = make_story_id("2026-03-19", "Iran intel minister")
    assert result == "2026-03-19-iran-intel-minister"


def test_make_story_id_lowercases():
    """The slug portion should be entirely lowercase."""
    from build import make_story_id

    result = make_story_id("2026-03-19", "TC39 Proposal")
    assert result == result.lower()


def test_make_story_id_strips_non_alphanumeric():
    """Non-alphanumeric characters (other than hyphens/spaces) are removed."""
    from build import make_story_id

    result = make_story_id("2026-03-19", "Iran's intelligence minister killed in Israeli strike")
    # Apostrophe should be stripped
    assert "'" not in result
    assert result.startswith("2026-03-19-")


# ---------------------------------------------------------------------------
# 5. make_story_id handles special characters, apostrophes, etc.
# ---------------------------------------------------------------------------

def test_make_story_id_special_characters():
    """Punctuation, ampersands, and other special chars are stripped."""
    from build import make_story_id

    result = make_story_id("2026-01-01", "Tech & Developer: A New Hope!")
    # Should not contain &, :, or !
    assert "&" not in result
    assert ":" not in result
    assert "!" not in result
    # Should still contain the words
    assert "tech" in result
    assert "developer" in result


def test_make_story_id_truncates_long_titles():
    """Very long titles should be truncated to a reasonable length."""
    from build import make_story_id

    long_title = "A " + "very " * 50 + "long title"
    result = make_story_id("2026-01-01", long_title)
    # The full ID (date + slug) should be reasonably bounded
    assert len(result) < 120


def test_make_story_id_no_trailing_hyphen():
    """The slug should not end with a trailing hyphen after truncation."""
    from build import make_story_id

    long_title = "word " * 30
    result = make_story_id("2026-01-01", long_title)
    assert not result.endswith("-")


def test_make_story_id_collapses_multiple_hyphens():
    """Multiple consecutive spaces/stripped chars should not produce runs of
    hyphens."""
    from build import make_story_id

    result = make_story_id("2026-01-01", "Hello   ---   World")
    assert "--" not in result


# ---------------------------------------------------------------------------
# 6. insert_stories inserts all stories from a parsed script
# ---------------------------------------------------------------------------

def test_insert_stories_count(db, parsed):
    """All stories from the fixture should be inserted."""
    from build import insert_stories

    insert_stories(db, parsed)
    cursor = db.execute("SELECT COUNT(*) FROM stories")
    count = cursor.fetchone()[0]
    # The fixture has 4 stories (2 + 1 + 1)
    assert count == 4


def test_insert_stories_titles(db, parsed):
    """Inserted stories should have the correct titles."""
    from build import insert_stories

    insert_stories(db, parsed)
    cursor = db.execute("SELECT title FROM stories ORDER BY rowid")
    titles = [row[0] for row in cursor.fetchall()]
    assert "Iran's intelligence minister killed in Israeli strike" in titles
    assert "Ceasefire talks resume in Cairo" in titles
    assert "TC39 signals proposal to deprecate prototype inheritance" in titles
    assert "Verstappen tops final practice in Melbourne" in titles


def test_insert_stories_date_from_parsed(db, parsed):
    """Each story should carry the date from the parsed frontmatter."""
    from build import insert_stories

    insert_stories(db, parsed)
    cursor = db.execute("SELECT DISTINCT date FROM stories")
    dates = [row[0] for row in cursor.fetchall()]
    assert dates == ["2026-03-19"]


def test_insert_stories_section_populated(db, parsed):
    """Each story should have its section title stored."""
    from build import insert_stories

    insert_stories(db, parsed)
    cursor = db.execute(
        "SELECT section FROM stories WHERE title LIKE '%Iran%'"
    )
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "World News"

    cursor = db.execute(
        "SELECT section FROM stories WHERE title LIKE '%Verstappen%'"
    )
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "Formula 1"


# ---------------------------------------------------------------------------
# 7. insert_stories correctly maps boolean fields to 0/1
# ---------------------------------------------------------------------------

def test_insert_stories_boolean_false_maps_to_zero(db, parsed):
    """previously_covered=False should be stored as INTEGER 0."""
    from build import insert_stories

    insert_stories(db, parsed)
    cursor = db.execute(
        "SELECT previously_covered FROM stories WHERE title LIKE '%Iran%'"
    )
    assert cursor.fetchone()[0] == 0


def test_insert_stories_boolean_true_maps_to_one(db, parsed):
    """previously_covered=True should be stored as INTEGER 1."""
    from build import insert_stories

    insert_stories(db, parsed)
    cursor = db.execute(
        "SELECT previously_covered FROM stories WHERE title LIKE '%Ceasefire%'"
    )
    assert cursor.fetchone()[0] == 1


def test_insert_stories_historical_callback_maps_correctly(db, parsed):
    """historical_callback=True should be stored as INTEGER 1."""
    from build import insert_stories

    insert_stories(db, parsed)
    cursor = db.execute(
        "SELECT historical_callback FROM stories WHERE title LIKE '%Ceasefire%'"
    )
    assert cursor.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# 8. insert_stories populates the FTS index
# ---------------------------------------------------------------------------

def test_insert_stories_fts_populated(db, parsed):
    """After insertion, FTS queries should return matching stories."""
    from build import insert_stories

    insert_stories(db, parsed)
    cursor = db.execute(
        "SELECT title FROM stories_fts WHERE stories_fts MATCH 'prototype'"
    )
    results = cursor.fetchall()
    assert len(results) >= 1
    assert any("TC39" in row[0] for row in results)


def test_insert_stories_fts_body_searchable(db, parsed):
    """FTS should index body text, not just titles."""
    from build import insert_stories

    insert_stories(db, parsed)
    cursor = db.execute(
        "SELECT title FROM stories_fts WHERE stories_fts MATCH 'McLaren'"
    )
    results = cursor.fetchall()
    assert len(results) >= 1
    assert any("Verstappen" in row[0] for row in results)


# ---------------------------------------------------------------------------
# 9. insert_stories handles duplicate inserts gracefully
# ---------------------------------------------------------------------------

def test_insert_stories_duplicate_no_error(db, parsed):
    """Inserting the same parsed data twice should not raise an error."""
    from build import insert_stories

    insert_stories(db, parsed)
    insert_stories(db, parsed)  # Should not raise


def test_insert_stories_duplicate_no_double_count(db, parsed):
    """After inserting twice, the count should still be 4 (not 8)."""
    from build import insert_stories

    insert_stories(db, parsed)
    insert_stories(db, parsed)
    cursor = db.execute("SELECT COUNT(*) FROM stories")
    assert cursor.fetchone()[0] == 4


# ---------------------------------------------------------------------------
# 10. query_recent returns stories from the last N days
# ---------------------------------------------------------------------------

def test_query_recent_returns_recent_stories(db):
    """Stories within the last N days should be returned."""
    from build import query_recent

    today = date.today().isoformat()
    _insert_story_row(db, story_id="today-story", story_date=today,
                       title="Today Story")
    results = query_recent(db, days=3)
    assert len(results) == 1
    assert results[0]["title"] == "Today Story"


def test_query_recent_includes_boundary_day(db):
    """A story exactly N days ago should still be included."""
    from build import query_recent

    boundary = (date.today() - timedelta(days=3)).isoformat()
    _insert_story_row(db, story_id="boundary-story", story_date=boundary,
                       title="Boundary Story")
    results = query_recent(db, days=3)
    assert any(r["title"] == "Boundary Story" for r in results)


def test_query_recent_ordered_by_date_desc(db):
    """Results should be ordered by date descending."""
    from build import query_recent

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    _insert_story_row(db, story_id="older", story_date=yesterday,
                       title="Yesterday")
    _insert_story_row(db, story_id="newer", story_date=today,
                       title="Today")
    results = query_recent(db, days=3)
    assert results[0]["title"] == "Today"
    assert results[1]["title"] == "Yesterday"


# ---------------------------------------------------------------------------
# 11. query_recent does not return older stories
# ---------------------------------------------------------------------------

def test_query_recent_excludes_old_stories(db):
    """Stories older than the window should not be returned."""
    from build import query_recent

    old_date = (date.today() - timedelta(days=10)).isoformat()
    _insert_story_row(db, story_id="old-story", story_date=old_date,
                       title="Old Story")
    results = query_recent(db, days=3)
    assert len(results) == 0


# ---------------------------------------------------------------------------
# 12. query_historical returns FTS matches older than the recency window
# ---------------------------------------------------------------------------

def test_query_historical_returns_old_fts_matches(db):
    """FTS matches outside the recency window should be returned."""
    from build import query_historical

    old_date = (date.today() - timedelta(days=10)).isoformat()
    _insert_story_row(db, story_id="old-ceasefire", story_date=old_date,
                       title="Ceasefire talks begin",
                       body="Negotiations for ceasefire started in Cairo.")
    # Manually update FTS
    db.execute(
        "INSERT INTO stories_fts(rowid, title, body) "
        "SELECT rowid, title, body FROM stories WHERE id='old-ceasefire'"
    )
    db.commit()

    results = query_historical(db, "ceasefire", days_ago=3)
    assert len(results) >= 1
    assert results[0]["title"] == "Ceasefire talks begin"


# ---------------------------------------------------------------------------
# 13. query_historical does not return recent stories
# ---------------------------------------------------------------------------

def test_query_historical_excludes_recent(db):
    """Stories within the recency window should not appear in historical
    results, even if they match the search term."""
    from build import query_historical

    today = date.today().isoformat()
    _insert_story_row(db, story_id="recent-ceasefire", story_date=today,
                       title="Ceasefire update today",
                       body="Latest ceasefire developments.")
    db.execute(
        "INSERT INTO stories_fts(rowid, title, body) "
        "SELECT rowid, title, body FROM stories WHERE id='recent-ceasefire'"
    )
    db.commit()

    results = query_historical(db, "ceasefire", days_ago=3)
    assert len(results) == 0


# ---------------------------------------------------------------------------
# 14. query_historical limits results to 3
# ---------------------------------------------------------------------------

def test_query_historical_limits_to_three(db):
    """Even with many matches, query_historical should return at most 3."""
    from build import query_historical

    old_date = (date.today() - timedelta(days=10)).isoformat()
    for i in range(6):
        sid = f"ceasefire-{i}"
        _insert_story_row(db, story_id=sid, story_date=old_date,
                           title=f"Ceasefire round {i}",
                           body="Ceasefire negotiations continued.")
        db.execute(
            "INSERT INTO stories_fts(rowid, title, body) "
            f"SELECT rowid, title, body FROM stories WHERE id='{sid}'"
        )
    db.commit()

    results = query_historical(db, "ceasefire", days_ago=3)
    assert len(results) == 3


def test_query_historical_ordered_by_date_desc(db):
    """Historical results should be ordered by date descending."""
    from build import query_historical

    for i in range(3):
        d = (date.today() - timedelta(days=10 + i)).isoformat()
        sid = f"hist-{i}"
        _insert_story_row(db, story_id=sid, story_date=d,
                           title=f"Ceasefire chapter {i}",
                           body="Ceasefire talks.")
        db.execute(
            "INSERT INTO stories_fts(rowid, title, body) "
            f"SELECT rowid, title, body FROM stories WHERE id='{sid}'"
        )
    db.commit()

    results = query_historical(db, "ceasefire", days_ago=3)
    dates = [r["date"] for r in results]
    assert dates == sorted(dates, reverse=True)


# ---------------------------------------------------------------------------
# 15. rebuild_db replays episode files and populates the database
# ---------------------------------------------------------------------------

def test_rebuild_db_populates_from_episodes(tmp_path):
    """rebuild_db should parse all episodes/*/script.md and insert stories."""
    from build import rebuild_db
    import shutil

    db_path = tmp_path / "briefings.db"
    episodes_dir = tmp_path / "episodes"
    ep_dir = episodes_dir / "2026-03-19"
    ep_dir.mkdir(parents=True)
    shutil.copy(FIXTURE, ep_dir / "script.md")

    rebuild_db(db_path, episodes_dir)

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("SELECT COUNT(*) FROM stories")
    assert cursor.fetchone()[0] == 4
    conn.close()


# ---------------------------------------------------------------------------
# 16. rebuild_db clears existing data before rebuilding
# ---------------------------------------------------------------------------

def test_rebuild_db_clears_existing_data(tmp_path):
    """rebuild_db should drop and recreate, so stale rows disappear."""
    from build import rebuild_db, init_db
    import shutil

    db_path = tmp_path / "briefings.db"
    episodes_dir = tmp_path / "episodes"
    ep_dir = episodes_dir / "2026-03-19"
    ep_dir.mkdir(parents=True)
    shutil.copy(FIXTURE, ep_dir / "script.md")

    # First, seed the DB with a row that should be cleared on rebuild.
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO stories (id, date, title, body) "
        "VALUES ('stale-row', '2020-01-01', 'Stale', 'Old data')"
    )
    conn.commit()
    conn.close()

    rebuild_db(db_path, episodes_dir)

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("SELECT COUNT(*) FROM stories WHERE id='stale-row'")
    assert cursor.fetchone()[0] == 0
    # But the replayed stories should be there
    cursor = conn.execute("SELECT COUNT(*) FROM stories")
    assert cursor.fetchone()[0] == 4
    conn.close()
