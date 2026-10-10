"""Apple SpeechTranscriber helper on this Mac (marker real: needs macOS 26 + swiftc). Never asks for a
locale that is not installed, so no speech model download is triggered."""
import shutil
import subprocess
import sys

import languages
import pytest
from helpers import LIB

pytestmark = [pytest.mark.real,
              pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"), reason="macOS + swiftc")]


@pytest.fixture(scope="module")
def apple_asr(tmp_path_factory):
    exe = tmp_path_factory.mktemp("swift") / "apple_asr"
    subprocess.run(["swiftc", "-O", "-o", str(exe), str(LIB / "apple_asr.swift")], check=True)
    return exe


def locales(apple_asr, kind):
    out = subprocess.run([apple_asr, "--locales"], capture_output=True, text=True, check=True).stdout
    return next(line.split()[1:] for line in out.splitlines() if line.startswith(kind + ":"))


def test_every_mapped_locale_is_supported(apple_asr):
    supported = locales(apple_asr, "supported")
    for code in languages.TABLE:
        loc = languages.get(code).apple_locale
        assert loc is None or loc in supported, f"{code} → {loc}"


def test_unsupported_locale_exit_3(apple_asr, tmp_path):
    r = subprocess.run([apple_asr, "de-XX", "x.wav", str(tmp_path / "o.json")], capture_output=True, text=True)
    assert r.returncode == 3


def test_installed_locale_transcribes(apple_asr, tmp_path):
    if "en-US" not in locales(apple_asr, "installed"):
        pytest.skip("en-US speech model not installed")
    aiff, wav = tmp_path / "en.aiff", tmp_path / "en.wav"
    subprocess.run(["say", "-v", "Samantha", "-o", aiff, "Hello, this is a short test."], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", aiff, "-ac", "1", "-ar", "16000", wav], check=True)
    subprocess.run([apple_asr, "--timeout", "60", "en-US", wav, tmp_path / "o.json"], check=True)
    assert "short test" in (tmp_path / "o.json").read_text()
