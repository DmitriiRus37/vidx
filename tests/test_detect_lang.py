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


def test_three_languages_and_more():
    assert vote(Counter(uk=4, en=3, de=3), 10) == ["uk", "en", "de"]
    assert vote(Counter(ru=10, en=6, de=5, fr=1), 22) == ["ru", "en", "de"]  # fr: one clip only
    assert vote(Counter(ru=6, en=6, de=6, fr=6, es=6), 30) == ["ru", "en", "de", "fr", "es"]  # no limit


def test_main_votes_with_the_language_id_backend(tmp_path, monkeypatch, capsys):
    """detect_lang.py asks lid.py (not mlx_whisper directly) for each clip's language."""
    import sys
    import types
    import wave

    import detect_lang
    import numpy as np

    sr = 16000
    x = (0.3 * np.sin(np.arange(sr * 90) / sr * 2 * np.pi * 300)).astype(np.float32)  # 90 s → 5 clips
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())
    answers = iter(["uk", "en", "uk", "de", "en", "de"])

    def load(model):
        assert model == "turbo"  # the model id comes first (vidx lid_py)
        return lambda clip: {next(answers): 0.9, "pt": 0.1}

    monkeypatch.setitem(sys.modules, "lid", types.SimpleNamespace(load=load))
    monkeypatch.setattr(sys, "argv", ["detect_lang.py", "turbo", str(path)])
    detect_lang.main()
    assert capsys.readouterr().out.strip() == "uk,en"  # 5 clips: uk 2, en 2, de 1
