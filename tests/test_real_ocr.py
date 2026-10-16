"""Apple Vision OCR with the languages a run asks for (marker real: macOS + swiftc). Slides are rendered
with Pillow (Arial Unicode covers every script here; raqm shapes Arabic and Devanagari)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import languages
import pytest
from helpers import LIB
from PIL import Image, ImageDraw, ImageFont

FONT = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
pytestmark = [pytest.mark.real, pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("swiftc") or not FONT.exists(), reason="macOS + swiftc + font")]
TITLES = {"ru": "Деплой во вторник", "uk": "Їжак і ґанок", "ja": "週末の天気予報", "zh": "周末的天气预报",
          "ar": "طقس نهاية الأسبوع", "hi": "आज की बैठक", "de": "Größe und Übergabe"}


@pytest.fixture(scope="module")
def ocr(tmp_path_factory):
    exe = tmp_path_factory.mktemp("swift") / "ocr"
    subprocess.run(["swiftc", "-O", "-o", str(exe), str(LIB / "ocr.swift")], check=True)
    return exe


@pytest.mark.parametrize("lang", sorted(TITLES))
def test_slide_title_read_in_its_language(ocr, tmp_path, lang):
    im = Image.new("RGB", (1280, 720), "white")
    ImageDraw.Draw(im).text((80, 300), TITLES[lang], fill="black", font=ImageFont.truetype(str(FONT), 64))
    png = tmp_path / f"{lang}.png"
    im.save(png)
    want = languages.ocr_arg([lang])
    out = subprocess.run([ocr, want, png], capture_output=True, text=True, check=True).stdout
    text = json.loads(out)["text"].replace(" ", "")
    assert TITLES[lang].replace(" ", "") in text, f"{want}: {text}"


def slide(path, lines):
    im = Image.new("RGB", (1280, 720), "white")
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        d.text((80, 160 + 140 * i), line, fill="black", font=ImageFont.truetype(str(FONT), 56))
    im.save(path)


@pytest.mark.parametrize("codes, lines", [
    (["ru", "zh"], ["Погода на выходные", "周末的天气预报", "Деплой в Kubernetes"]),
    (["ru", "ar"], ["طقس نهاية الأسبوع", "Погода на выходные"]),
    (["de", "zh"], ["Größe und Übergabe", "周末的天气预报"]),
])
def test_mixed_script_slide_reads_every_line_once(ocr, tmp_path, codes, lines):
    """With ru-RU first Vision returned nothing for the Chinese line; the zh model alone turns Cyrillic
    into Latin look-alikes ("Aennon B Kubernetes"). One request per script, merged by position: each
    line exactly once, from the model of its own script."""
    png = tmp_path / "s.png"
    slide(png, lines)
    out = subprocess.run([ocr, languages.ocr_arg(codes), png], capture_output=True, text=True, check=True).stdout
    assert json.loads(out)["text"].splitlines() == lines


def test_unreadable_image_is_reported(ocr, tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_text("not an image", encoding="utf-8", newline="\n")
    r = subprocess.run([ocr, "en-US", bad], capture_output=True, text=True, check=True)
    assert json.loads(r.stdout) == {"file": "bad.jpg", "text": ""}
    assert "bad.jpg" in r.stderr
