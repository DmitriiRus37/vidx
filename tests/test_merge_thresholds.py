"""Speech-rate thresholds per language: normal speech is kept, a chunk at 1/3 of it is replaced.

Normal rates: whisper's chars/s on the TTS probe clips (de 17.9; fr/it/es 18-21 from the plan's probe;
be assumed like uk), except ru/en: real meeting speech (~15), which today's 6/25 was tuned on.
"""
import languages
import pytest
from merge import skipped_speech

NORMAL_CPS = {"ru": 15.0, "en": 15.0, "de": 17.9, "fr": 19.0, "it": 19.0, "es": 19.0, "uk": 13.8, "be": 13.8,
              "ja": 6.6, "zh": 4.5, "hi": 15.4, "ar": 8.0}
DUR = 30.0  # long enough that the alternative passes ALT_MIN_S x normal_cps at every rate


@pytest.mark.parametrize("lang", sorted(NORMAL_CPS))
def test_normal_speech_kept_slow_chunk_replaced(lang):
    row, cps = languages.get(lang), NORMAL_CPS[lang]
    alt = round(cps * DUR)
    assert not skipped_speech(round(cps * DUR), alt * 3, DUR, row)
    assert skipped_speech(round(cps / 3 * DUR), alt, DUR, row)


@pytest.mark.parametrize("lang", sorted(NORMAL_CPS))
def test_alternative_faster_than_loop_cps_rejected(lang):
    row = languages.get(lang)
    assert not skipped_speech(10, round(row.loop_cps * DUR) + 1, DUR, row)
    assert row.loop_cps > 1.4 * NORMAL_CPS[lang]  # fast speakers are not taken for loops


def test_latin_and_cyrillic_keep_todays_thresholds():
    for lang in ("ru", "en", "uk", "be"):
        assert (languages.get(lang).skip_cps, languages.get(lang).loop_cps) == (6.0, 25.0)


def test_arabic_loop_limit_is_16_chars_per_second():
    """Arabic speech runs at ~8 chars/s: an alternative at 15 chars/s is speech, at 20 a repetition loop
    (the Latin limit, 25, would let it through)."""
    ar = languages.get("ar")
    assert skipped_speech(10, round(15 * DUR), DUR, ar)
    assert not skipped_speech(10, round(20 * DUR), DUR, ar)


def test_exactly_at_skip_cps_is_not_skipped_speech():
    ru = languages.get("ru")
    assert not skipped_speech(60, 200, 10.0, ru)  # 6.0 chars/s: the floor itself is normal enough
    assert skipped_speech(59, 200, 10.0, ru)
