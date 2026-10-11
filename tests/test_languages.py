"""lib/languages.py: one table with everything vidx knows per language."""
import re
import subprocess
import sys

import languages
import pytest
from helpers import LIB

LISTED = ["ru", "en", "de", "fr", "it", "es", "ja", "zh", "hi", "be", "uk", "ar"]
PARAKEET_25 = {"bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it", "lv", "lt", "mt",
               "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk"}
FIELDS = {"script": str, "apple_locale": (str, type(None)), "ocr_langs": tuple, "vosk_model": (str, type(None)),
          "parakeet": bool, "joiner": str, "fillers": (str, type(None)), "skip_cps": float, "loop_cps": float,
          "normal_cps": float}


@pytest.mark.parametrize("code", LISTED)
def test_every_listed_language_has_every_field(code):
    row = languages.get(code)
    for field, kind in FIELDS.items():
        assert isinstance(getattr(row, field), kind), f"{code}.{field}"
    assert row.script in languages.SCRIPTS
    assert row.ocr_langs, f"{code}: no OCR language"
    if row.fillers:
        re.compile(row.fillers)


def test_parakeet_exactly_its_25_languages():
    assert len(PARAKEET_25) == 25
    assert languages.PARAKEET == PARAKEET_25
    for code in LISTED + ["pl", "ko", "pt", "tr"]:
        assert languages.get(code).parakeet == (code in PARAKEET_25), code


def test_unknown_code_gets_whisper_only_defaults():
    ko = languages.get("ko")
    assert ko.script == "hangul"
    assert (ko.apple_locale, ko.vosk_model, ko.parakeet, ko.fillers) == (None, None, False, None)
    xx = languages.get("xx")  # not even a known script: latin defaults
    assert (xx.script, xx.joiner, xx.skip_cps) == ("latin", " ", 6.0)


def test_cli_prints_one_field():
    def cli(*args):
        return subprocess.run([sys.executable, "-I", str(LIB / "languages.py"), *args], capture_output=True,
                              text=True, encoding="utf-8", check=True).stdout.strip()

    assert cli("de", "apple_locale") == "de-DE"
    assert cli("be", "apple_locale") == ""  # None → empty
    assert cli("ru", "vosk_model") == "vosk-model-ru-0.42"
    assert cli("zh", "parakeet") == "0"
    assert cli("uk", "parakeet") == "1"
    assert cli("be", "ocr_langs") == "ru-RU,uk-UA"


def test_cli_prints_several_fields():
    """vidx reads all it needs about a language in one call: values joined by |, empty ones kept."""
    def cli(*args):
        return subprocess.run([sys.executable, "-I", str(LIB / "languages.py"), *args], capture_output=True,
                              text=True, encoding="utf-8", check=True).stdout

    assert cli("be", "parakeet", "apple_locale", "vosk_model") == "0||\n"
    assert cli("de", "parakeet", "apple_locale", "vosk_model") == "1|de-DE|vosk-model-de-0.21\n"


def test_cli_rejects_unknown_field():
    r = subprocess.run([sys.executable, "-I", str(LIB / "languages.py"), "ru", "nope"], capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 2


APPLE = {"en": "en-US", "de": "de-DE", "fr": "fr-FR", "it": "it-IT", "es": "es-ES", "ja": "ja-JP", "zh": "zh-CN",
         "hi": "hi-IN", "ru": None, "uk": None, "be": None, "ar": None}


@pytest.mark.parametrize("code", sorted(APPLE))
def test_apple_locale_is_explicit(code):
    """"de" alone resolved to de-AT (and a model download that hung): always pass language-REGION."""
    assert languages.get(code).apple_locale == APPLE[code]


def test_every_row_spells_out_its_settings():
    """One place to edit: a listed language's row names all of its own settings (keywords, no positions)."""
    own = {"script", "apple_locale", "ocr_langs", "vosk_model", "fillers"}
    for code, row in languages.TABLE.items():
        assert own <= row.keys(), code
        assert set(row) <= set(languages.Lang.__dataclass_fields__), code


@pytest.mark.parametrize("code", ["th", "lo", "my", "km"])
def test_scripts_without_word_spaces(code):
    """Thai, Lao, Burmese and Khmer write no spaces between words: chunks join without one."""
    row = languages.get(code)
    assert row.joiner == "" and row.script != "latin"
    assert row.loop_cps > 3 * row.skip_cps  # alphabets with vowel signs, not CJK's 2/10 chars/s


@pytest.mark.parametrize("codes, groups", [
    (["ru"], [["ru-RU", "en-US"]]),
    (["ru", "en"], [["ru-RU", "en-US"]]),  # Vision's Latin and Cyrillic models read both alphabets
    (["ja"], [["ja-JP", "en-US"]]),
    (["ko"], [["en-US"]]),  # no Vision language of its own
    (["zh", "de"], [["de-DE", "en-US"], ["zh-Hans", "en-US"]]),  # Latin first: zh-Hans reads Größe as GroBe
    (["ru", "zh"], [["ru-RU", "en-US"], ["zh-Hans", "en-US"]]),
    (["ja", "zh"], [["ja-JP", "zh-Hans", "en-US"]]),  # the CJK models read each other's characters
    (["hi", "ru", "ar", "uk"], [["ru-RU", "uk-UA", "en-US"], ["hi-IN", "en-US"], ["ar-SA", "en-US"]]),
])
def test_ocr_groups_one_request_per_script(codes, groups):
    assert languages.ocr_groups(codes) == groups


def test_cli_ocr_argument():
    r = subprocess.run([sys.executable, "-I", str(LIB / "languages.py"), "--ocr", "ru,zh"], capture_output=True,
                       text=True, encoding="utf-8", check=True)
    assert r.stdout.strip() == "ru-RU,en-US;zh-Hans,en-US"
