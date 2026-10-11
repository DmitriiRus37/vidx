"""The Linux/Windows backends with real (small) models (marker real). They run where `vidx setup` made the
engine venvs of this platform (VIDX_HOME or the checkout); elsewhere (macOS) they skip.

Models: whisper VIDX_TEST_WHISPER_MODEL (default tiny, 75 MB); parakeet nemo-parakeet-tdt-0.6b-v3 int8 on the
CPU (0.6 GB); RapidOCR's PP-OCR models (~50 MB). Downloaded into the Hugging Face / vidx caches on first use.
"""
import json
import os
import re
import subprocess

import pytest
from helpers import FIXTURES, LIB, MEDIA

from vidx import paths
from vidx.engines import Registry

pytestmark = pytest.mark.real
REG = Registry(paths.platform_of(), paths.home())
WHISPER_MODEL = os.environ.get("VIDX_TEST_WHISPER_MODEL", "tiny")
MLX_SEGMENT_KEYS = {"id", "seek", "start", "end", "text", "tokens", "temperature", "avg_logprob", "compression_ratio",
                    "no_speech_prob"}


def python_of(engine):
    """The engine's program; skips where it is not installed, and on macOS for the Linux/Windows backends
    (GigaAM is the same script everywhere, so it runs on macOS too)."""
    e = REG.get(engine)
    if e is None or (REG.platform == "darwin" and engine != "gigaam") or not os.path.exists(e.program[0]):
        pytest.skip(f"{engine}: backend not installed here (vidx setup)")
    return e.program


def wav(media, tmp_path):
    out = tmp_path / "audio.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", MEDIA / media, "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", out], check=True)
    return out


def test_whisper_fw_russian_clip(tmp_path):
    prog = python_of("whisper")
    subprocess.run([*prog, wav("ru_en.m4a", tmp_path), "--model", WHISPER_MODEL, "-o", tmp_path, "--output-name", "w",
                    "-f", "json", "--verbose", "False", "--condition-on-previous-text", "False", "--language", "ru",
                    "--clip-timestamps", "0,17.8"], check=True)
    out = json.loads((tmp_path / "w.json").read_text(encoding="utf-8"))
    segs = out["segments"]
    assert segs and all(set(s) == MLX_SEGMENT_KEYS for s in segs)
    assert [s["start"] for s in segs] == sorted(s["start"] for s in segs) and segs[-1]["end"] <= 18.5
    assert re.search("[а-яё]", out["text"].lower()) and out["language"] == "ru"


def test_language_id_tells_english_from_russian(tmp_path):
    prog = python_of("whisper")
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import lid, common; sr, x = common.read_wav(sys.argv[3]);"
            "d = lid.load(sys.argv[2]); a = d(x[0:sr * 8]); b = d(x[sr * 21:sr * 28]);"
            "print(max(a, key=a.get), max(b, key=b.get))")
    r = subprocess.run([prog[0], "-I", "-c", code, str(LIB), WHISPER_MODEL, wav("ru_en.m4a", tmp_path)],
                       capture_output=True, text=True, check=True)
    assert r.stdout.split() == ["ru", "en"]


def test_parakeet_onnx_sentences(tmp_path):
    prog = python_of("parakeet")
    subprocess.run([*prog, wav("ru_en.m4a", tmp_path), "--model", REG.get("parakeet").model, "--output-format",
                    "json", "--output-dir", tmp_path, "--output-template", "parakeet"], check=True)
    out = json.loads((tmp_path / "parakeet.json").read_text(encoding="utf-8"))
    first = out["sentences"][0]
    assert first["text"] == "Добрый вечер." and first["start"] < 0.5
    assert len(out["sentences"]) >= 8
    assert any("English" in s["text"] for s in out["sentences"])  # parakeet is not told a language
    starts = [s["start"] for s in out["sentences"]]
    assert starts == sorted(starts) and all(s["end"] > s["start"] for s in out["sentences"])


def test_gigaam_on_this_platform(tmp_path):
    prog = python_of("gigaam")
    subprocess.run([*prog, wav("ru_en.m4a", tmp_path), tmp_path / "gigaam.json"], check=True)
    out = json.loads((tmp_path / "gigaam.json").read_text(encoding="utf-8"))
    assert out["engine"] == "gigaam" and "Добрый вечер" in out["segments"][0]["text"]


# tests/fixtures/slides: rendered on macOS with Arial Unicode, the titles of tests/test_real_ocr.py
TITLES = {"ru": "Деплой во вторник", "uk": "Їжак і ґанок", "ja": "週末の天気予報", "zh": "周末的天气预报",
          "ar": "طقس نهاية الأسبوع", "hi": "आज की बैठक", "de": "Größe und Übergabe"}


def rapid():
    e = REG.get("ocr")
    if REG.platform == "darwin" or not os.path.exists(e.program[0]):
        pytest.skip("RapidOCR backend not installed here (vidx setup)")
    return e


def rapid_ocr(codes, png):
    import languages

    e = rapid()
    r = subprocess.run(e.argv(languages.ocr_arg(codes), png), env={**os.environ, **e.env}, capture_output=True,
                       text=True, encoding="utf-8", check=True)
    return json.loads(r.stdout)["text"]


@pytest.mark.parametrize("lang", sorted(TITLES))
def test_rapidocr_slide_title(lang):
    rapid()
    if lang == "hi":
        pytest.xfail("RapidOCR's detectors do not find Devanagari titles (docs/plans/2026-10-11-porting-analysis.md)")
    text = rapid_ocr([lang], FIXTURES / "slides" / f"{lang}.png")
    assert TITLES[lang].replace(" ", "") in text.replace(" ", "")


@pytest.mark.parametrize("codes, name, lines", [
    (["ru", "zh"], "ru_zh", ["Погода на выходные", "周末的天气预报", "Деплой в Kubernetes"]),
    (["ru", "ar"], "ru_ar", ["طقس نهاية الأسبوع", "Погода на выходные"]),
    (["de", "zh"], "de_zh", ["Größe und Übergabe", "周末的天气预报"]),
])
def test_rapidocr_mixed_script_slide_reads_every_line_once(codes, name, lines):
    assert rapid_ocr(codes, FIXTURES / "slides" / f"{name}.png").splitlines() == lines
