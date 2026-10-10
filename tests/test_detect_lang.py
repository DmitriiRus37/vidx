"""detect_lang.vote: which detected languages count."""
from collections import Counter

from detect_lang import vote


def test_one_misdetected_clip_does_not_make_it_mixed():
    assert vote(Counter(ru=4, en=1), 5) == ["ru"]


def test_second_language_needs_two_clips_and_20_percent():
    assert vote(Counter(ru=3, en=2), 5) == ["ru", "en"]
    assert vote(Counter(ru=27, en=2), 30) == ["ru"]  # 2 clips but < 20 %
    assert vote(Counter(ru=24, en=6), 30) == ["ru", "en"]


def test_no_speech_means_ru():
    assert vote(Counter(), 5) == ["ru"]
