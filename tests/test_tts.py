"""Tests for TTS audio generation and chapter timestamps.

Covers: prepare_tts_text, generate_audio, extract_chapters, write_chapters.

Since pytest-asyncio is not available in this environment, async tests use
asyncio.run() directly within standard pytest functions.

Spec ambiguities recorded as ticket notes:
- The edge-tts library reports offsets in 100-nanosecond ticks (not seconds).
  The spec says generate_audio returns offsets "in seconds", so the
  implementation must convert. Tests assume seconds in the returned data.
- The spec does not define what "pause markers" look like in prepare_tts_text.
  Tests check for some non-empty separator between story bodies rather than
  a specific string.
- The spec does not clarify whether the outro/sentinel text is included in
  TTS output. Tests assume it is not.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the project root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE = Path(__file__).parent / "fixtures" / "sample_script.md"


# ---------------------------------------------------------------------------
# Helper: parsed fixture data (reused across tests)
# ---------------------------------------------------------------------------

@pytest.fixture()
def parsed():
    from build import parse_script

    return parse_script(FIXTURE)


# ---------------------------------------------------------------------------
# Helper: sample word-level timing data for chapter extraction tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_timings():
    """Simulated word-level timing data matching the sample fixture's stories.

    Story titles from the fixture:
      s1: "Iran's intelligence minister killed in Israeli strike"
      s2: "Ceasefire talks resume in Cairo"
      s3: "TC39 signals proposal to deprecate prototype inheritance"
      s4: "Verstappen tops final practice in Melbourne"

    We place some preamble words (the intro), then the title words for each
    story at known offsets, followed by body words.
    """
    timings = [
        # Intro words
        {"text": "A", "offset": 0.0},
        {"text": "tense", "offset": 0.3},
        {"text": "day", "offset": 0.6},
        # Story 1 title words (starting at offset 2.0)
        {"text": "Iran's", "offset": 2.0},
        {"text": "intelligence", "offset": 2.4},
        {"text": "minister", "offset": 2.9},
        {"text": "killed", "offset": 3.3},
        {"text": "in", "offset": 3.6},
        {"text": "Israeli", "offset": 3.8},
        {"text": "strike", "offset": 4.2},
        # Story 1 body words
        {"text": "Iran's", "offset": 4.6},
        {"text": "intelligence", "offset": 5.0},
        {"text": "minister", "offset": 5.4},
        {"text": "was", "offset": 5.8},
        # Story 2 title words (starting at offset 20.0)
        {"text": "Ceasefire", "offset": 20.0},
        {"text": "talks", "offset": 20.4},
        {"text": "resume", "offset": 20.8},
        {"text": "in", "offset": 21.1},
        {"text": "Cairo", "offset": 21.4},
        # Story 2 body words
        {"text": "Ceasefire", "offset": 22.0},
        {"text": "negotiations", "offset": 22.5},
        # Story 3 title words (starting at offset 45.0)
        {"text": "TC39", "offset": 45.0},
        {"text": "signals", "offset": 45.4},
        {"text": "proposal", "offset": 45.8},
        {"text": "to", "offset": 46.1},
        {"text": "deprecate", "offset": 46.4},
        {"text": "prototype", "offset": 46.9},
        {"text": "inheritance", "offset": 47.3},
        # Story 3 body words
        {"text": "A", "offset": 48.0},
        {"text": "stage-one", "offset": 48.3},
        # Story 4 title words (starting at offset 70.0)
        {"text": "Verstappen", "offset": 70.0},
        {"text": "tops", "offset": 70.5},
        {"text": "final", "offset": 70.8},
        {"text": "practice", "offset": 71.2},
        {"text": "in", "offset": 71.5},
        {"text": "Melbourne", "offset": 71.8},
        # Story 4 body words
        {"text": "Max", "offset": 72.5},
        {"text": "Verstappen", "offset": 73.0},
    ]
    return timings


# ---------------------------------------------------------------------------
# 1. Import sanity
# ---------------------------------------------------------------------------

def test_prepare_tts_text_is_importable():
    """prepare_tts_text should be importable from build."""
    from build import prepare_tts_text  # noqa: F401


def test_generate_audio_is_importable():
    """generate_audio should be importable from build."""
    from build import generate_audio  # noqa: F401


def test_extract_chapters_is_importable():
    """extract_chapters should be importable from build."""
    from build import extract_chapters  # noqa: F401


def test_write_chapters_is_importable():
    """write_chapters should be importable from build."""
    from build import write_chapters  # noqa: F401


# ---------------------------------------------------------------------------
# 2. prepare_tts_text — concatenates story bodies with intro
# ---------------------------------------------------------------------------

def test_prepare_tts_text_contains_all_story_bodies(parsed):
    """The concatenated TTS text should contain every story body."""
    from build import prepare_tts_text

    result = prepare_tts_text(parsed)
    for section in parsed["sections"]:
        for story in section["stories"]:
            assert story["body"] in result


def test_prepare_tts_text_includes_intro(parsed):
    """The TTS text should contain the intro text."""
    from build import prepare_tts_text

    result = prepare_tts_text(parsed)
    assert parsed["intro"] in result


def test_prepare_tts_text_stories_separated_by_pause(parsed):
    """Stories should be separated by some pause marker, not run together."""
    from build import prepare_tts_text

    result = prepare_tts_text(parsed)
    stories = [
        story
        for section in parsed["sections"]
        for story in section["stories"]
    ]
    # Check that story bodies are not directly adjacent — there should be
    # something between the end of one body and the start of the next.
    for i in range(len(stories) - 1):
        body_a = stories[i]["body"]
        body_b = stories[i + 1]["body"]
        end_of_a = result.index(body_a) + len(body_a)
        start_of_b = result.index(body_b)
        separator = result[end_of_a:start_of_b]
        assert len(separator.strip()) > 0, (
            f"No pause marker found between story {i} and story {i + 1}"
        )


def test_prepare_tts_text_preserves_section_order(parsed):
    """Stories should appear in the same order as the sections."""
    from build import prepare_tts_text

    result = prepare_tts_text(parsed)
    stories = [
        story
        for section in parsed["sections"]
        for story in section["stories"]
    ]
    last_pos = -1
    for story in stories:
        pos = result.index(story["body"])
        assert pos > last_pos, (
            f"Story '{story['title']}' appears out of order in TTS text"
        )
        last_pos = pos


# ---------------------------------------------------------------------------
# 3. generate_audio — voice selection
# ---------------------------------------------------------------------------

def test_generate_audio_uses_correct_voice(tmp_path):
    """generate_audio should instantiate edge_tts.Communicate with en-GB-RyanNeural."""
    from build import generate_audio

    output_path = tmp_path / "audio.mp3"

    mock_communicate = MagicMock()

    async def empty_stream():
        return
        yield  # make it an async generator

    mock_communicate.return_value.stream = empty_stream
    mock_communicate.return_value.save = AsyncMock()

    with patch("edge_tts.Communicate", mock_communicate):
        asyncio.run(generate_audio("Hello world", output_path))

    # Check that Communicate was called with the correct voice
    mock_communicate.assert_called_once()
    call_kwargs = mock_communicate.call_args
    args, kwargs = call_kwargs
    # Voice could be positional or keyword
    voice_used = kwargs.get("voice") or (args[1] if len(args) > 1 else None)
    assert voice_used == "en-GB-RyanNeural"


# ---------------------------------------------------------------------------
# 4. generate_audio — writes MP3 file
# ---------------------------------------------------------------------------

def test_generate_audio_writes_mp3(tmp_path):
    """generate_audio should write an MP3 file to the specified output path."""
    from build import generate_audio

    output_path = tmp_path / "audio.mp3"

    async def mock_stream():
        yield {"type": "audio", "data": b"\xff\xfb\x90\x00" * 10}
        yield {
            "type": "WordBoundary",
            "offset": 5_000_000,
            "duration": 2_000_000,
            "text": "Hello",
        }

    mock_communicate_instance = MagicMock()
    mock_communicate_instance.stream = mock_stream

    with patch("edge_tts.Communicate", return_value=mock_communicate_instance):
        asyncio.run(generate_audio("Hello world", output_path))

    assert output_path.exists(), "MP3 file was not created"
    assert output_path.stat().st_size > 0, "MP3 file is empty"


# ---------------------------------------------------------------------------
# 5. generate_audio — returns word-level timing data
# ---------------------------------------------------------------------------

def test_generate_audio_returns_word_timings(tmp_path):
    """generate_audio should return a list of dicts with 'text' and 'offset' keys,
    with offsets converted to seconds."""
    from build import generate_audio

    output_path = tmp_path / "audio.mp3"

    async def mock_stream():
        yield {"type": "audio", "data": b"\xff\xfb\x90\x00"}
        yield {
            "type": "WordBoundary",
            "offset": 5_000_000,
            "duration": 2_000_000,
            "text": "Hello",
        }
        yield {"type": "audio", "data": b"\xff\xfb\x90\x00"}
        yield {
            "type": "WordBoundary",
            "offset": 10_000_000,
            "duration": 3_000_000,
            "text": "world",
        }

    mock_communicate_instance = MagicMock()
    mock_communicate_instance.stream = mock_stream

    with patch("edge_tts.Communicate", return_value=mock_communicate_instance):
        timings = asyncio.run(generate_audio("Hello world", output_path))

    assert isinstance(timings, list)
    assert len(timings) == 2
    for entry in timings:
        assert "text" in entry
        assert "offset" in entry
    assert timings[0]["text"] == "Hello"
    assert timings[1]["text"] == "world"
    # Offsets should be in seconds (converted from 100ns ticks)
    assert timings[0]["offset"] == pytest.approx(0.5, abs=0.01)
    assert timings[1]["offset"] == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# 6. generate_audio — retry on failure, re-raise on second failure
# ---------------------------------------------------------------------------

def test_generate_audio_retries_once_then_raises(tmp_path):
    """generate_audio should retry once on failure, then re-raise on the
    second failure."""
    from build import generate_audio

    output_path = tmp_path / "audio.mp3"

    call_count = 0

    def make_communicate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        instance = MagicMock()

        async def failing_stream():
            raise RuntimeError("TTS service unavailable")
            yield  # make it an async generator

        instance.stream = failing_stream
        instance.save = AsyncMock(
            side_effect=RuntimeError("TTS service unavailable"),
        )
        return instance

    with patch("edge_tts.Communicate", side_effect=make_communicate):
        with pytest.raises(RuntimeError, match="TTS service unavailable"):
            asyncio.run(generate_audio("Hello world", output_path))

    # Should have been called exactly twice (initial + one retry)
    assert call_count == 2, (
        f"Expected exactly 2 attempts (1 retry), got {call_count}"
    )


# ---------------------------------------------------------------------------
# 7. generate_audio — succeeds on retry
# ---------------------------------------------------------------------------

def test_generate_audio_succeeds_on_retry(tmp_path):
    """If the first attempt fails but the second succeeds, generate_audio
    should return timing data without raising."""
    from build import generate_audio

    output_path = tmp_path / "audio.mp3"

    call_count = 0

    def make_communicate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        instance = MagicMock()

        if call_count == 1:
            async def failing_stream():
                raise RuntimeError("Transient failure")
                yield  # noqa: E501

            instance.stream = failing_stream
            instance.save = AsyncMock(
                side_effect=RuntimeError("Transient failure"),
            )
        else:
            async def success_stream():
                yield {"type": "audio", "data": b"\xff\xfb\x90\x00"}
                yield {
                    "type": "WordBoundary",
                    "offset": 5_000_000,
                    "duration": 2_000_000,
                    "text": "Hello",
                }

            instance.stream = success_stream
            instance.save = AsyncMock()

        return instance

    with patch("edge_tts.Communicate", side_effect=make_communicate):
        timings = asyncio.run(generate_audio("Hello world", output_path))

    assert isinstance(timings, list)
    assert len(timings) >= 1


# ---------------------------------------------------------------------------
# 8. extract_chapters — correct IDs
# ---------------------------------------------------------------------------

def test_extract_chapters_ids(parsed, sample_timings):
    """Chapter IDs should be sequential: s1, s2, s3, ..."""
    from build import extract_chapters

    chapters = extract_chapters(parsed, sample_timings)
    expected_ids = [f"s{i}" for i in range(1, len(chapters) + 1)]
    actual_ids = [ch["id"] for ch in chapters]
    assert actual_ids == expected_ids


# ---------------------------------------------------------------------------
# 9. extract_chapters — matches story titles to timing data
# ---------------------------------------------------------------------------

def test_extract_chapters_timestamps(parsed, sample_timings):
    """Chapter start timestamps should correspond to where title words appear
    in the timing data."""
    from build import extract_chapters

    chapters = extract_chapters(parsed, sample_timings)

    # From sample_timings fixture:
    # Story 1 title starts at 2.0s
    # Story 2 title starts at 20.0s
    # Story 3 title starts at 45.0s
    # Story 4 title starts at 70.0s
    assert chapters[0]["start"] == pytest.approx(2.0, abs=0.5)
    assert chapters[1]["start"] == pytest.approx(20.0, abs=0.5)
    assert chapters[2]["start"] == pytest.approx(45.0, abs=0.5)
    assert chapters[3]["start"] == pytest.approx(70.0, abs=0.5)


def test_extract_chapters_titles(parsed, sample_timings):
    """Each chapter should carry the correct story title."""
    from build import extract_chapters

    chapters = extract_chapters(parsed, sample_timings)
    expected_titles = [
        "Iran's intelligence minister killed in Israeli strike",
        "Ceasefire talks resume in Cairo",
        "TC39 signals proposal to deprecate prototype inheritance",
        "Verstappen tops final practice in Melbourne",
    ]
    actual_titles = [ch["title"] for ch in chapters]
    assert actual_titles == expected_titles


# ---------------------------------------------------------------------------
# 10. extract_chapters — correct section names
# ---------------------------------------------------------------------------

def test_extract_chapters_sections(parsed, sample_timings):
    """Each chapter should include the section name it belongs to."""
    from build import extract_chapters

    chapters = extract_chapters(parsed, sample_timings)
    expected_sections = [
        "World News",
        "World News",
        "Tech & Developer",
        "Formula 1",
    ]
    actual_sections = [ch["section"] for ch in chapters]
    assert actual_sections == expected_sections


# ---------------------------------------------------------------------------
# 11. write_chapters — writes valid JSON
# ---------------------------------------------------------------------------

def test_write_chapters_creates_json_file(tmp_path):
    """write_chapters should write a valid JSON file to the output path."""
    from build import write_chapters

    chapters = [
        {"id": "s1", "title": "Test Story", "section": "News", "start": 0.0},
    ]
    output_path = tmp_path / "chapters.json"
    write_chapters(chapters, output_path)

    assert output_path.exists(), "chapters.json was not created"
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# 12. write_chapters — output matches expected schema
# ---------------------------------------------------------------------------

def test_write_chapters_schema(tmp_path):
    """Each chapter in the JSON output should have id, title, section, start."""
    from build import write_chapters

    chapters = [
        {"id": "s1", "title": "Story One", "section": "World", "start": 1.5},
        {"id": "s2", "title": "Story Two", "section": "Tech", "start": 30.2},
    ]
    output_path = tmp_path / "chapters.json"
    write_chapters(chapters, output_path)

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(data) == 2
    for chapter in data:
        assert "id" in chapter
        assert "title" in chapter
        assert "section" in chapter
        assert "start" in chapter
        assert isinstance(chapter["id"], str)
        assert isinstance(chapter["title"], str)
        assert isinstance(chapter["section"], str)
        assert isinstance(chapter["start"], (int, float))

    assert data[0]["id"] == "s1"
    assert data[0]["title"] == "Story One"
    assert data[0]["section"] == "World"
    assert data[0]["start"] == pytest.approx(1.5)
    assert data[1]["id"] == "s2"


# ---------------------------------------------------------------------------
# 13. extract_chapters — fuzzy matching handles TTS punctuation
# ---------------------------------------------------------------------------

def test_extract_chapters_matches_despite_punctuation():
    """edge-tts word boundaries often strip apostrophes, trailing commas,
    and other punctuation. extract_chapters should still match titles."""
    from build import extract_chapters

    parsed = {
        "date": "2026-03-19",
        "duration_estimate": "5 minutes",
        "intro": "Good morning.",
        "sections": [
            {
                "title": "World News",
                "stories": [
                    {"title": "Iran's intelligence minister killed in strike",
                     "body": "Details of the strike."},
                ],
            },
            {
                "title": "Tech & Developer",
                "stories": [
                    {"title": "Astral, Makers of Ruff and uv, Join OpenAI",
                     "body": "Astral is joining OpenAI."},
                ],
            },
        ],
    }

    # Simulate edge-tts stripping apostrophes and trailing commas
    timings = [
        {"text": "Good", "offset": 0.0},
        {"text": "morning.", "offset": 0.3},
        # Story 1: TTS returns "Irans" instead of "Iran's"
        {"text": "Irans", "offset": 2.0},
        {"text": "intelligence", "offset": 2.4},
        {"text": "minister", "offset": 2.8},
        {"text": "killed", "offset": 3.2},
        {"text": "in", "offset": 3.5},
        {"text": "strike", "offset": 3.8},
        {"text": "Details", "offset": 4.5},
        # Story 2: TTS strips trailing commas from "Astral," and "uv,"
        {"text": "Astral", "offset": 20.0},
        {"text": "Makers", "offset": 20.4},
        {"text": "of", "offset": 20.7},
        {"text": "Ruff", "offset": 20.9},
        {"text": "and", "offset": 21.1},
        {"text": "uv", "offset": 21.3},
        {"text": "Join", "offset": 21.6},
        {"text": "OpenAI", "offset": 21.9},
        {"text": "Astral", "offset": 22.5},
    ]

    chapters = extract_chapters(parsed, timings)
    assert len(chapters) == 2, f"Expected 2 chapters, got {len(chapters)}"
    assert chapters[0]["start"] == pytest.approx(2.0)
    assert chapters[1]["start"] == pytest.approx(20.0)


def test_extract_chapters_from_sentence_boundaries():
    """When timings contain sentence boundaries (modern edge-tts),
    extract_chapters should match titles against sentence text."""
    from build import extract_chapters

    parsed = {
        "date": "2026-03-19",
        "duration_estimate": "7 minutes",
        "intro": "Good morning.",
        "sections": [
            {
                "title": "World News",
                "stories": [
                    {"title": "Europe Sleepwalks into Another Energy Crisis",
                     "body": "The Bank of England held rates."},
                ],
            },
            {
                "title": "Hungary",
                "stories": [
                    {"title": "Hungary Deepens Ukraine Rift as Election Nears",
                     "body": "Viktor Orban continues to block."},
                ],
            },
        ],
    }

    timings = [
        {"text": "Good morning.", "offset": 0.1, "type": "sentence"},
        {"text": "Europe Sleepwalks into Another Energy Crisis.", "offset": 3.5, "type": "sentence"},
        {"text": "The Bank of England held rates.", "offset": 8.2, "type": "sentence"},
        {"text": "Hungary Deepens Ukraine Rift as Election Nears.", "offset": 25.0, "type": "sentence"},
        {"text": "Viktor Orban continues to block.", "offset": 30.1, "type": "sentence"},
    ]

    chapters = extract_chapters(parsed, timings)
    assert len(chapters) == 2
    assert chapters[0]["title"] == "Europe Sleepwalks into Another Energy Crisis"
    assert chapters[0]["start"] == pytest.approx(3.5)
    assert chapters[1]["title"] == "Hungary Deepens Ukraine Rift as Election Nears"
    assert chapters[1]["start"] == pytest.approx(25.0)


def test_extract_chapters_matches_despite_curly_apostrophe():
    """edge-tts may return curly apostrophes (\\u2019) while titles use
    straight ones. Matching should normalise both."""
    from build import extract_chapters

    parsed = {
        "date": "2026-03-19",
        "duration_estimate": "3 minutes",
        "intro": "Hello.",
        "sections": [{
            "title": "News",
            "stories": [{"title": "UK Music's Big Win", "body": "Details."}],
        }],
    }

    timings = [
        {"text": "Hello.", "offset": 0.0},
        {"text": "UK", "offset": 2.0},
        {"text": "Music\u2019s", "offset": 2.3},
        {"text": "Big", "offset": 2.6},
        {"text": "Win", "offset": 2.9},
        {"text": "Details.", "offset": 3.5},
    ]

    chapters = extract_chapters(parsed, timings)
    assert len(chapters) == 1
    assert chapters[0]["start"] == pytest.approx(2.0)
