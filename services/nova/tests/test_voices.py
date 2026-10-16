"""Nova's voice shortlist, checked without calling LiveKit."""

import wave

import pytest

from nova import script
from nova.voices import CANDIDATES, sample_lines, words_per_minute, write_wav


def test_three_candidates_from_three_providers():
    assert len(CANDIDATES) == 3
    assert len({c.model.split("/")[0] for c in CANDIDATES}) == 3


def test_the_samples_are_approved_lines_only():
    lines = sample_lines()
    assert script.NEED_A_MOMENT in lines and script.INVITATION in lines
    assert len(lines) == 6


def test_words_per_minute_compares_with_the_brand_books_150():
    assert words_per_minute(["one two three four five"] * 5, seconds=10.0) == pytest.approx(150)


def test_samples_are_written_as_mono_16_bit_wav(tmp_path):
    path = tmp_path / "voice.wav"
    write_wav(path, [b"\x01\x00" * 240, b"\x02\x00" * 240], sample_rate=24000)
    with wave.open(str(path), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
        assert wav.getnframes() == 480
