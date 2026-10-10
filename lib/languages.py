"""Everything vidx knows per spoken language, in one table (runs in every vidx Python, stdlib only).

usage: languages.py <code> <field>      → the field's value: lists comma-joined, bool as 1/0, None as ""
       languages.py --ocr <code,code>   → Apple Vision OCR languages for a run: union of ocr_langs, plus en-US

Per language:
  script        latin, cyrillic, cjk, arabic, devanagari or hangul; sets the defaults below
  apple_locale  SpeechTranscriber locale (explicit, so "de" never resolves to de-AT), None = not supported
  ocr_langs     Apple Vision recognition languages for on-screen text
  vosk_model    Vosk model directory name (alphacephei.com), None = no model
  parakeet      in parakeet-tdt-0.6b-v3's 25 languages (elsewhere it outputs gibberish)
  joiner        between merged chunks: " ", or "" for scripts written without spaces
  fillers       regex of one hesitation sound (with its trailing comma/space), None = none removed
  skip_cps      below this many chars/s a base chunk may have skipped speech (merge fallback)
  loop_cps      above this many chars/s an alternative is probably a repetition loop
A code not in the table (any other whisper language) gets whisper-only defaults for its script.
"""
import sys
from dataclasses import dataclass

# Word boundaries that also work next to Cyrillic (shared with the glossary's <W> and <E>)
W = r"(?<![\wА-Яа-яЁё])"
E = r"(?![\wА-Яа-яЁё])"


AFTER_FILLER = r"[,.、，،]?\s*"  # the comma (Latin, CJK or Arabic) and spaces that go with it
# Chinese and Japanese have no spaces, so \w boundaries never match there: a filler must stand at the
# start or after punctuation instead.
CJK_START = r"(?:^|(?<=[、。，,.!?！？\s]))"


def filler_re(*alternatives, cjk=False):
    """One hesitation sound as a whole word, with the comma and spaces after it."""
    if cjk:
        return CJK_START + "(?:" + "|".join(alternatives) + ")" + AFTER_FILLER
    return W + "(?:" + "|".join(alternatives) + ")" + E + AFTER_FILLER


# Hesitation sounds only, never a word of the language: German "um" (= around), Belarusian "ем"
# (= I eat), Japanese "あの" (= that) and "ええ" (= yes) are words and stay.
RU_FILLERS = (r"[ИиНн]у?-[иу]+", r"[АаЭэ]+-[аэ]+(?:-[аэ]+)*", r"[Аа]{3,}", r"[Ээ]{3,}", r"[Хх]м+", r"[Мм]{3,}", r"м-м")
EN_FILLERS = (r"[Uu]h+", r"[Uu]m+", r"[Uu]hm", r"[Ee]rm")
FILLERS = {
    "ru": filler_re(*RU_FILLERS, *EN_FILLERS),  # ru and en meetings mix: both sets, as before
    "en": filler_re(*RU_FILLERS, *EN_FILLERS),
    "de": filler_re(r"[Ää]h+m*", r"[Öö]h+m*", r"[Hh]m+"),
    "fr": filler_re(r"[Ee]uh+", r"[Hh]eu+", r"[Bb]euh"),
    "es": filler_re(r"[Ee]h+m*", r"[Mm]{3,}"),
    "it": filler_re(r"[Ee]h+m*", r"[Mm]{3,}"),
    "uk": filler_re(r"[Ее]+-[ее]+(?:-[ее]+)*", r"[Ее]{3,}", r"[Ее]м+(?=,)", r"[Мм]{3,}", r"[Гг]м+", r"[Хх]м+"),
    "be": filler_re(r"[ЭэЕе]+-[эе]+(?:-[эе]+)*", r"[Ээ]{3,}", r"[Мм]{3,}", r"[Гг]м+", r"[Хх]м+"),
    "ja": filler_re(r"えーと", r"えっと", r"えー+", r"あのー+", r"あの(?=、)", cjk=True),
    "zh": filler_re(r"呃+", r"嗯+(?=[，,。])", cjk=True),
    "ar": filler_re(r"آ{2,}", r"[اإ]?م{3,}"),
}

# script: (joiner, skip_cps, loop_cps). whisper on the TTS probe clips, normal speech in chars/s:
# ru 17, uk 13.8, en 19, de/fr/it/es 18-21, devanagari 15.4, arabic 8.0, Japanese 6.6, Chinese 4.5.
# skip_cps sits 2.2-3x below normal (a chunk at 1/3 of the normal rate counts as skipped speech),
# loop_cps 1.4-2x above. Latin/Cyrillic keep the 6/25 tuned on real Russian meetings (~15 chars/s);
# the faster Romance/German TTS speech gets its own row. hangul is not calibrated (no clip).
SCRIPTS = {
    "latin": (" ", 6.0, 25.0),
    "cyrillic": (" ", 6.0, 25.0),
    "arabic": (" ", 3.0, 16.0),
    "devanagari": (" ", 6.0, 25.0),
    "cjk": ("", 2.0, 10.0),
    "hangul": (" ", 3.0, 16.0),
}
# language: (skip_cps, loop_cps) where the script default does not fit
THRESHOLDS = {"ja": (2.5, 12.0), "de": (7.0, 30.0), "fr": (7.0, 30.0), "it": (7.0, 30.0), "es": (7.0, 30.0)}
# parakeet-tdt-0.6b-v3 (model card): 25 European languages
PARAKEET = {"bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it", "lv", "lt", "mt",
            "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk"}
# script of whisper languages outside the table that are not written in Latin letters
OTHER_SCRIPTS = {
    "bg": "cyrillic", "kk": "cyrillic", "mk": "cyrillic", "mn": "cyrillic", "sr": "cyrillic", "tg": "cyrillic",
    "tt": "cyrillic", "ba": "cyrillic",
    "fa": "arabic", "ur": "arabic", "ps": "arabic", "sd": "arabic",
    "mr": "devanagari", "ne": "devanagari", "sa": "devanagari",
    "yue": "cjk", "ko": "hangul",
}


@dataclass(frozen=True)
class Lang:
    code: str
    script: str
    apple_locale: str | None = None
    ocr_langs: tuple = ("en-US",)
    vosk_model: str | None = None
    parakeet: bool = False
    joiner: str = " "
    fillers: str | None = None
    skip_cps: float = 6.0
    loop_cps: float = 25.0


# code: script, apple_locale, ocr_langs, vosk_model (fillers: FILLERS)
TABLE = {
    "ru": ("cyrillic", None, ("ru-RU",), "vosk-model-ru-0.42"),
    "en": ("latin", "en-US", ("en-US",), "vosk-model-en-us-0.22"),
    "de": ("latin", "de-DE", ("de-DE",), "vosk-model-de-0.21"),
    "fr": ("latin", "fr-FR", ("fr-FR",), "vosk-model-fr-0.22"),
    "it": ("latin", "it-IT", ("it-IT",), "vosk-model-it-0.22"),
    "es": ("latin", "es-ES", ("es-ES",), "vosk-model-es-0.42"),
    "ja": ("cjk", "ja-JP", ("ja-JP",), "vosk-model-ja-0.22"),
    "zh": ("cjk", "zh-CN", ("zh-Hans",), "vosk-model-cn-0.22"),
    "hi": ("devanagari", "hi-IN", ("hi-IN",), "vosk-model-hi-0.22"),
    "be": ("cyrillic", None, ("ru-RU", "uk-UA"), None),  # Vision has no be: closest Cyrillic
    "uk": ("cyrillic", None, ("uk-UA",), "vosk-model-uk-v3"),
    "ar": ("arabic", None, ("ar-SA",), "vosk-model-ar-mgb2-0.4"),
}


def get(code):
    """Table row for a language code; unknown codes get whisper-only defaults for their script."""
    if code in TABLE:
        script, apple, ocr, vosk = TABLE[code]
    else:
        script, apple, ocr, vosk = OTHER_SCRIPTS.get(code, "latin"), None, ("en-US",), None
    fillers = FILLERS.get(code)
    joiner, skip, loop = SCRIPTS[script]
    skip, loop = THRESHOLDS.get(code, (skip, loop))
    return Lang(code, script, apple, ocr, vosk, code in PARAKEET, joiner, fillers, skip, loop)


def ocr_langs(codes):
    """Vision OCR languages for a run: every language's ocr_langs in order, then en-US, no repeats."""
    res = []
    for code in codes:
        res += [x for x in get(code).ocr_langs if x not in res]
    return res + ([] if "en-US" in res else ["en-US"])


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--ocr":
        print(",".join(ocr_langs(sys.argv[2].split(","))))
        return
    if len(sys.argv) != 3 or sys.argv[2] not in Lang.__dataclass_fields__:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        sys.exit(2)
    v = getattr(get(sys.argv[1]), sys.argv[2])
    print(",".join(v) if isinstance(v, tuple) else int(v) if isinstance(v, bool) else "" if v is None else v)


if __name__ == "__main__":
    main()
