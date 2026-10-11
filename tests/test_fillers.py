"""Hesitation sounds per language: removed from the transcript; real words with the same letters stay."""
import json
import re

import languages
import pytest
from helpers import run_merge

CASES = {  # lang: [(raw, cleaned)]
    "ru": [("Э-э, мы начинаем. Ммм, дальше", "Мы начинаем. Дальше"), ("Эммануил ест", "Эммануил ест")],
    "en": [("Uh, we start. Um, next", "We start. Next"), ("Umbrella, uhuru", "Umbrella, uhuru")],
    "de": [("Äh, wir haben, ähm, das gemacht", "Wir haben, das gemacht"),
           ("Wir treffen uns um acht, ähnlich wie öfter", "Wir treffen uns um acht, ähnlich wie öfter"),
           ("Hm, wir sehen. Hmm, gut", "Wir sehen. Gut"), ("Hmmel", "Hmmel")],
    "fr": [("Euh, on commence. Euh on verra", "On commence. On verra"), ("Une heure en Europe", "Une heure en Europe")],
    "es": [("Eh, bueno, ehm, vamos", "Bueno, vamos"), ("Lo hecho, ehh", "Lo hecho")],
    "it": [("Ehm, allora, eh, andiamo", "Allora, andiamo"), ("Ehi, è vero", "Ehi, è vero")],
    "uk": [("Е-е, ми почнемо. Ем, далі", "Ми почнемо. Далі"), ("Емоції та ємність", "Емоції та ємність")],
    "be": [("Э-э, мы пачнем. Ммм, далей", "Мы пачнем. Далей"), ("Я ем хлеб", "Я ем хлеб")],
    "ja": [("えー、今日は、あのー、データの話です", "今日は、データの話です"),
           ("あの人は、ええ、そうです", "あの人は、ええ、そうです")],
    "zh": [("嗯，今天我们，呃，聊一聊天气", "今天我们，聊一聊天气"), ("我们感恩", "我们感恩")],
    "ar": [("آآ، اليوم سنتحدث", "اليوم سنتحدث"), ("آمن ومستقر", "آمن ومستقر")],
    "hi": [("आज हम मौसम के बारे में बात करेंगे", "आज हम मौसम के बारे में बात करेंगे")],
}


@pytest.mark.parametrize("lang", sorted(CASES))
def test_fillers_removed_words_kept(lang, tmp_path):
    out = tmp_path / f"{lang}_vidx-out"
    (out / "asr").mkdir(parents=True)
    segs = [{"start": 10 * i, "end": 10 * i + 5, "text": raw} for i, (raw, _) in enumerate(CASES[lang])]
    (out / "asr" / "whisper-large-v3.json").write_text(json.dumps({"segments": segs}, ensure_ascii=False))
    run_merge(out, "--langs", lang)
    lines = [re.sub(r"^\[[\d:]+\] ", "", x) for x in (out / "transcript.txt").read_text().splitlines()]
    assert lines == [want for _, want in CASES[lang]]


def test_fillers_follow_the_region_language(tmp_path):
    """German "um" is a word; in an English region of the same run it is a filler."""
    out = tmp_path / "mix_vidx-out"
    (out / "asr").mkdir(parents=True)
    for lang, text in (("de", "Wir treffen uns um acht."), ("en", "Um, we meet at eight.")):
        seg = {"start": 0 if lang == "de" else 10, "end": 5 if lang == "de" else 15, "text": text}
        (out / "asr" / f"whisper-large-v3@{lang}.json").write_text(json.dumps({"segments": [seg]}))
    (out / "regions.json").write_text(json.dumps([{"start": 0, "end": 8, "lang": "de"},
                                                  {"start": 8, "end": 20, "lang": "en"}]))
    run_merge(out, "--langs", "de,en")
    assert "Wir treffen uns um acht. We meet at eight." in (out / "transcript.md").read_text()


def test_every_listed_language_but_hi_has_fillers():
    for code in ("ru", "en", "de", "fr", "it", "es", "ja", "zh", "be", "uk", "ar"):
        assert languages.get(code).fillers, code
