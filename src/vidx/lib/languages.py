"""Everything vidx knows per spoken language, in one table (runs in every vidx Python, stdlib only).

usage: languages.py <code> <field>...   → the fields' values joined by "|": lists comma-joined, bool as 1/0,
                                          None as "" (so vidx reads a language's settings in one call)
       languages.py --ocr <code,code>   → Apple Vision OCR languages for a run, one group per script
                                          (ru-RU,en-US;zh-Hans,en-US), see ocr_groups()

Per language:
  script        latin, cyrillic, cjk, arabic, devanagari, hangul, thai, lao, myanmar or khmer; sets the
                defaults below
  apple_locale  SpeechTranscriber locale (explicit, so "de" never resolves to de-AT), None = not supported
  ocr_langs     Apple Vision recognition languages for on-screen text
  vosk_model    Vosk model directory name (alphacephei.com), None = no model
  parakeet      in parakeet-tdt-0.6b-v3's 25 languages (elsewhere it outputs gibberish)
  joiner        between merged chunks: " ", or "" for scripts written without spaces
  fillers       regex of one hesitation sound (with its trailing comma/space), None = none removed
  skip_cps      below this many chars/s a base chunk may have skipped speech (merge fallback)
  loop_cps      above this many chars/s an alternative is probably a repetition loop
  normal_cps    usual speech rate of the script; the merge needs an alternative of at least a few seconds
                of it (merge.ALT_MIN_S) before swapping a base chunk
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
RU_EN_FILLERS = filler_re(*RU_FILLERS, *EN_FILLERS)  # ru and en meetings mix: both sets in both languages

# Per script: joiner, skip_cps, loop_cps, normal_cps. whisper on the TTS probe clips, normal speech in chars/s:
# ru 17, uk 13.8, en 19, de/fr/it/es 18-21, devanagari 15.4, arabic 8.0, Japanese 6.6, Chinese 4.5.
# skip_cps sits 2.2-3x below normal (a chunk at 1/3 of the normal rate counts as skipped speech),
# loop_cps 1.4-2x above; normal_cps is the low end of normal (Latin/Cyrillic 16, so 5 s = the 80 chars
# the merge always asked for). Latin/Cyrillic keep the 6/25 tuned on real Russian meetings (~15 chars/s);
# the faster Romance/German TTS speech gets its own values in TABLE. hangul and the Southeast Asian
# scripts are not calibrated (no clip).
SCRIPTS = {
    "latin": dict(joiner=" ", skip_cps=6.0, loop_cps=25.0, normal_cps=16.0),
    "cyrillic": dict(joiner=" ", skip_cps=6.0, loop_cps=25.0, normal_cps=16.0),
    "arabic": dict(joiner=" ", skip_cps=3.0, loop_cps=16.0, normal_cps=8.0),
    "devanagari": dict(joiner=" ", skip_cps=6.0, loop_cps=25.0, normal_cps=15.0),
    "cjk": dict(joiner="", skip_cps=2.0, loop_cps=10.0, normal_cps=4.5),
    "hangul": dict(joiner=" ", skip_cps=3.0, loop_cps=16.0, normal_cps=8.0),
    # Southeast Asian scripts: no spaces between words; not calibrated (no clip), devanagari's rates
    **{name: dict(joiner="", skip_cps=6.0, loop_cps=25.0, normal_cps=15.0)
       for name in ("thai", "lao", "myanmar", "khmer")},
}
FAST_SPEECH = dict(skip_cps=7.0, loop_cps=30.0)  # de/fr/it/es: 18-21 chars/s on the TTS clips

# One row per listed language: everything vidx knows about it, in one place. The joiner and the
# thresholds come from the row's script unless the row sets them.
TABLE = {
    "ru": dict(script="cyrillic", apple_locale=None, ocr_langs=("ru-RU",), vosk_model="vosk-model-ru-0.42",
               fillers=RU_EN_FILLERS),
    "en": dict(script="latin", apple_locale="en-US", ocr_langs=("en-US",), vosk_model="vosk-model-en-us-0.22",
               fillers=RU_EN_FILLERS),
    "de": dict(script="latin", apple_locale="de-DE", ocr_langs=("de-DE",), vosk_model="vosk-model-de-0.21",
               fillers=filler_re(r"[Ää]h+m*", r"[Öö]h+m*", r"[Hh]m+"), **FAST_SPEECH),
    "fr": dict(script="latin", apple_locale="fr-FR", ocr_langs=("fr-FR",), vosk_model="vosk-model-fr-0.22",
               fillers=filler_re(r"[Ee]uh+", r"[Hh]eu+", r"[Bb]euh"), **FAST_SPEECH),
    "it": dict(script="latin", apple_locale="it-IT", ocr_langs=("it-IT",), vosk_model="vosk-model-it-0.22",
               fillers=filler_re(r"[Ee]h+m*", r"[Mm]{3,}"), **FAST_SPEECH),
    "es": dict(script="latin", apple_locale="es-ES", ocr_langs=("es-ES",), vosk_model="vosk-model-es-0.42",
               fillers=filler_re(r"[Ee]h+m*", r"[Mm]{3,}"), **FAST_SPEECH),
    "ja": dict(script="cjk", apple_locale="ja-JP", ocr_langs=("ja-JP",), vosk_model="vosk-model-ja-0.22",
               fillers=filler_re(r"えーと", r"えっと", r"えー+", r"あのー+", r"あの(?=、)", cjk=True),
               skip_cps=2.5, loop_cps=12.0),  # 6.6 chars/s, faster than Chinese
    "zh": dict(script="cjk", apple_locale="zh-CN", ocr_langs=("zh-Hans",), vosk_model="vosk-model-cn-0.22",
               fillers=filler_re(r"呃+", r"嗯+(?=[，,。])", cjk=True)),
    "hi": dict(script="devanagari", apple_locale="hi-IN", ocr_langs=("hi-IN",), vosk_model="vosk-model-hi-0.22",
               fillers=None),
    "be": dict(script="cyrillic", apple_locale=None, ocr_langs=("ru-RU", "uk-UA"),  # Vision has no be
               vosk_model=None, fillers=filler_re(r"[ЭэЕе]+-[эе]+(?:-[эе]+)*", r"[Ээ]{3,}", r"[Мм]{3,}", r"[Гг]м+",
                                                  r"[Хх]м+")),
    "uk": dict(script="cyrillic", apple_locale=None, ocr_langs=("uk-UA",), vosk_model="vosk-model-uk-v3",
               fillers=filler_re(r"[Ее]+-[ее]+(?:-[ее]+)*", r"[Ее]{3,}", r"[Ее]м+(?=,)", r"[Мм]{3,}", r"[Гг]м+",
                                 r"[Хх]м+")),
    "ar": dict(script="arabic", apple_locale=None, ocr_langs=("ar-SA",), vosk_model="vosk-model-ar-mgb2-0.4",
               fillers=filler_re(r"آ{2,}", r"[اإ]?م{3,}")),
}
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
    "th": "thai", "lo": "lao", "my": "myanmar", "km": "khmer",
}


@dataclass(frozen=True)
class Lang:
    code: str
    script: str
    apple_locale: str | None
    ocr_langs: tuple
    vosk_model: str | None
    parakeet: bool
    joiner: str
    fillers: str | None
    skip_cps: float
    loop_cps: float
    normal_cps: float


def get(code):
    """Row of a language code; a code outside TABLE gets whisper-only defaults for its script."""
    row = TABLE.get(code) or dict(script=OTHER_SCRIPTS.get(code, "latin"), apple_locale=None, ocr_langs=("en-US",),
                                  vosk_model=None, fillers=None)
    return Lang(code=code, parakeet=code in PARAKEET, **{**SCRIPTS[row["script"]], **row})


def ocr_groups(codes):
    """Vision OCR languages for a run, one group per Vision request. Vision reads only the script of a
    request's first language (plus Latin), so each script gets its own group: Latin and Cyrillic first
    (their models read both alphabets), then the others in the order of codes; CJK is one group (ja-JP
    and zh-Hans read each other's characters). Every group ends with en-US; no language twice."""
    groups = {}
    for code in codes:
        row = get(code)
        key = "alphabetic" if row.script in ("latin", "cyrillic") or row.ocr_langs == ("en-US",) else row.script
        g = groups.setdefault(key, [])
        g += [x for x in row.ocr_langs if x not in g]
    order = sorted(groups, key=lambda k: k != "alphabetic")  # stable: the rest keep the order of codes
    return [[x for x in groups[k] if x != "en-US"] + ["en-US"] for k in order]


def ocr_arg(codes):
    """ocr_groups as the ocr helper's first argument: "ru-RU,en-US;zh-Hans,en-US"."""
    return ";".join(",".join(g) for g in ocr_groups(codes))


def main():
    for stream in (sys.stdout, sys.stderr):  # UTF-8 and LF on every OS (Windows: the code page, CRLF)
        stream.reconfigure(encoding="utf-8", newline="\n")
    if len(sys.argv) == 3 and sys.argv[1] == "--ocr":
        print(ocr_arg(sys.argv[2].split(",")))
        return
    fields = sys.argv[2:]
    if not fields or not set(fields) <= set(Lang.__dataclass_fields__):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        sys.exit(2)
    row = get(sys.argv[1])

    def text(v):
        if isinstance(v, tuple):
            return ",".join(v)
        return str(int(v)) if isinstance(v, bool) else "" if v is None else str(v)

    print("|".join(text(getattr(row, f)) for f in fields))


if __name__ == "__main__":
    main()
