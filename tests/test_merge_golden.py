"""Characterisation tests: fixed engine outputs → the transcript merge.py makes today.

ru_en is the real ru+en+ru test video (asr/ from an actual run, trimmed to start/end/text);
ru is the same video run as Russian only. A diff here means merge behaviour changed: update the
golden files deliberately with VIDX_UPDATE_GOLDENS=1 and review the diff.
"""
import pytest
from helpers import check_golden, out_dir_from, run_merge

RU_EN_SOURCES = ("parakeet,gigaam,apple@en,vosk@en,vosk@ru,whisper-large-v3@en,whisper-large-v3@ru,"
                 "whisper-turbo@en,whisper-turbo@ru")
CASES = {
    "ru": ("merge/ru", ["--langs", "ru"]),
    "ru_en_regions": ("merge/ru_en", ["--langs", "ru,en", "--sources", RU_EN_SOURCES]),
    "ru_en_cut": ("merge/ru_en", ["--langs", "ru,en", "--sources", RU_EN_SOURCES, "--cut", "17.84-30.4"]),
    "repeats": ("merge/repeats", ["--langs", "ru"]),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden(case, tmp_path):
    fixture, args = CASES[case]
    out = run_merge(out_dir_from(fixture, tmp_path), *args)
    check_golden(case, out)
