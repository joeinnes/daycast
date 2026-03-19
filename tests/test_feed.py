"""Tests for RSS podcast feed generation (render_feed).

Covers: valid XML, required RSS/iTunes/Podlove elements, episode items,
enclosure tags, and chapter support.
"""

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _minimal_script(date: str, duration: str) -> str:
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


_NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "psc": "http://podlove.org/simple-chapters",
    "atom": "http://www.w3.org/2005/Atom",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def feed_config():
    return {
        "repo": "joeinnes/daycast",
        "title": "Daycast",
        "description": "A daily news briefing.",
        "site_url": "https://joeinnes.github.io/daycast",
        "language": "en-gb",
        "author": "Daycast",
    }


@pytest.fixture()
def episodes_dir(tmp_path):
    """Create a tmp episodes directory with two episodes and chapters."""
    for date_str, dur in [("2026-03-18", "5 minutes"), ("2026-03-19", "7 minutes")]:
        ep = tmp_path / date_str
        ep.mkdir()
        (ep / "script.md").write_text(_minimal_script(date_str, dur))
        (ep / "audio.mp3").write_bytes(b"\xff\xfb\x90\x00" * 100)

    import json
    (tmp_path / "2026-03-19" / "chapters.json").write_text(json.dumps([
        {"id": "s1", "title": "A Story", "section": "Section One", "start": 0.0},
        {"id": "s2", "title": "Another Story", "section": "Section Two", "start": 30.5},
    ]))
    (tmp_path / "2026-03-18" / "chapters.json").write_text(json.dumps([]))

    return tmp_path


@pytest.fixture()
def feed_xml(episodes_dir, feed_config):
    from build import render_feed
    return render_feed(episodes_dir, feed_config)


@pytest.fixture()
def root(feed_xml):
    return ET.fromstring(feed_xml)


# ---------------------------------------------------------------------------
# 1. Import sanity
# ---------------------------------------------------------------------------

def test_render_feed_is_importable():
    from build import render_feed  # noqa: F401


# ---------------------------------------------------------------------------
# 2. Returns valid XML string
# ---------------------------------------------------------------------------

def test_returns_string(feed_xml):
    assert isinstance(feed_xml, str)


def test_valid_xml(feed_xml):
    ET.fromstring(feed_xml)  # should not raise


# ---------------------------------------------------------------------------
# 3. RSS channel metadata
# ---------------------------------------------------------------------------

def test_channel_title(root):
    assert root.find("channel/title").text == "Daycast"


def test_channel_description(root):
    assert root.find("channel/description").text == "A daily news briefing."


def test_channel_language(root):
    assert root.find("channel/language").text == "en-gb"


def test_channel_link(root):
    assert "joeinnes.github.io/daycast" in root.find("channel/link").text


# ---------------------------------------------------------------------------
# 4. iTunes namespace elements
# ---------------------------------------------------------------------------

def test_itunes_author(root):
    author = root.find("channel/{%s}author" % _NS["itunes"])
    assert author is not None
    assert author.text == "Daycast"


def test_itunes_category_exists(root):
    cat = root.find("channel/{%s}category" % _NS["itunes"])
    assert cat is not None


# ---------------------------------------------------------------------------
# 5. Episode items
# ---------------------------------------------------------------------------

def test_has_two_items(root):
    items = root.findall("channel/item")
    assert len(items) == 2


def test_items_newest_first(root):
    items = root.findall("channel/item")
    titles = [item.find("title").text for item in items]
    # Newest (2026-03-19) should come first
    assert "2026-03-19" in titles[0]


def test_item_has_title(root):
    item = root.findall("channel/item")[0]
    assert item.find("title") is not None
    assert item.find("title").text


def test_item_has_enclosure(root):
    item = root.findall("channel/item")[0]
    enc = item.find("enclosure")
    assert enc is not None
    assert enc.get("type") == "audio/mpeg"
    assert enc.get("url").endswith(".mp3")


def test_item_has_guid(root):
    item = root.findall("channel/item")[0]
    guid = item.find("guid")
    assert guid is not None
    assert guid.text


def test_item_has_pubdate(root):
    item = root.findall("channel/item")[0]
    assert item.find("pubDate") is not None


def test_item_has_itunes_duration(root):
    item = root.findall("channel/item")[0]
    dur = item.find("{%s}duration" % _NS["itunes"])
    assert dur is not None
    assert dur.text


def test_item_has_description(root):
    item = root.findall("channel/item")[0]
    desc = item.find("description")
    assert desc is not None
    assert desc.text


# ---------------------------------------------------------------------------
# 6. Podlove Simple Chapters
# ---------------------------------------------------------------------------

def test_item_has_psc_chapters(root):
    """The episode with chapters should have psc:chapters elements."""
    items = root.findall("channel/item")
    # First item is 2026-03-19 which has chapters
    chapters_el = items[0].find("{%s}chapters" % _NS["psc"])
    assert chapters_el is not None


def test_psc_chapter_count(root):
    items = root.findall("channel/item")
    chapters_el = items[0].find("{%s}chapters" % _NS["psc"])
    ch_items = chapters_el.findall("{%s}chapter" % _NS["psc"])
    assert len(ch_items) == 2


def test_psc_chapter_attributes(root):
    items = root.findall("channel/item")
    chapters_el = items[0].find("{%s}chapters" % _NS["psc"])
    ch = chapters_el.findall("{%s}chapter" % _NS["psc"])[0]
    assert ch.get("start") is not None
    assert ch.get("title") == "A Story"


def test_psc_chapter_start_format(root):
    """Chapter start times should be in HH:MM:SS.mmm format."""
    items = root.findall("channel/item")
    chapters_el = items[0].find("{%s}chapters" % _NS["psc"])
    ch = chapters_el.findall("{%s}chapter" % _NS["psc"])[1]
    # 30.5s should be "00:00:30.500"
    assert ch.get("start") == "00:00:30.500"


def test_episode_without_chapters_has_no_psc(root):
    """Episodes with empty chapters should not have a psc:chapters element."""
    items = root.findall("channel/item")
    # Second item is 2026-03-18 which has no chapters
    chapters_el = items[1].find("{%s}chapters" % _NS["psc"])
    assert chapters_el is None


# ---------------------------------------------------------------------------
# 7. Feed is written to docs by copy_latest_episode
# ---------------------------------------------------------------------------

def test_feed_xml_written_to_docs(tmp_path, feed_config):
    """render_feed output should be saveable to docs/feed.xml."""
    from build import render_feed

    ep = tmp_path / "episodes" / "2026-03-19"
    ep.mkdir(parents=True)
    (ep / "script.md").write_text(_minimal_script("2026-03-19", "7 minutes"))
    (ep / "audio.mp3").write_bytes(b"\xff\xfb\x90\x00" * 10)
    (ep / "chapters.json").write_text("[]")

    xml = render_feed(tmp_path / "episodes", feed_config)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "feed.xml").write_text(xml, encoding="utf-8")

    assert (docs / "feed.xml").exists()
    ET.fromstring((docs / "feed.xml").read_text())


# ---------------------------------------------------------------------------
# 8. itunes:duration format (ticket day-5iul)
# ---------------------------------------------------------------------------

def test_itunes_duration_is_hhmmss_format(root):
    """Every <itunes:duration> must be HH:MM:SS, not a human string."""
    import re
    items = root.findall("channel/item")
    for item in items:
        dur = item.find("{%s}duration" % _NS["itunes"])
        assert dur is not None
        assert re.match(r"^\d{2}:\d{2}:\d{2}$", dur.text), (
            f"Expected HH:MM:SS but got {dur.text!r}"
        )


def test_itunes_duration_is_not_frontmatter_estimate(root):
    """<itunes:duration> must not be the frontmatter duration_estimate string."""
    items = root.findall("channel/item")
    # The fixture episodes have duration_estimate values "7 minutes" and "5 minutes".
    human_strings = {"5 minutes", "7 minutes"}
    for item in items:
        dur = item.find("{%s}duration" % _NS["itunes"])
        assert dur is not None
        assert dur.text not in human_strings, (
            f"Duration should come from the audio file, not frontmatter (got {dur.text!r})"
        )


def test_itunes_duration_is_nonzero(root):
    """<itunes:duration> must not be 00:00:00 — it should reflect actual
    audio length."""
    items = root.findall("channel/item")
    for item in items:
        dur = item.find("{%s}duration" % _NS["itunes"])
        assert dur is not None
        assert dur.text != "00:00:00", (
            "Duration must not be zero — should reflect audio file length"
        )
