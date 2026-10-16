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
    r = subprocess.run([apple_asr, "--timeout", "60", "de-XX", "x.wav", str(tmp_path / "o.json")],
                       capture_output=True, text=True)
    assert r.returncode == 3


@pytest.mark.parametrize("t", ["inf", "nan", "1e12", "86401", "0", "-1", "x"])
def test_timeout_out_of_range_is_a_usage_error(apple_asr, tmp_path, t):
    """1..86400 s only: inf or 1e12 seconds would trap converting to nanoseconds. en-US (installed with
    macOS) so that a helper that wrongly accepted the value still never starts a model download."""
    r = subprocess.run([apple_asr, "--timeout", t, "en-US", "x.wav", str(tmp_path / "o.json")],
                       capture_output=True, text=True)
    assert r.returncode == 2, r.stderr


def test_timeout_is_required(apple_asr, tmp_path):
    """vidx owns the default (VIDX_APPLE_TIMEOUT), so the helper has none of its own."""
    r = subprocess.run([apple_asr, "en-US", "x.wav", str(tmp_path / "o.json")], capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr


def test_installed_locale_transcribes(apple_asr, tmp_path):
    if "en-US" not in locales(apple_asr, "installed"):
        pytest.skip("en-US speech model not installed")
    aiff, wav = tmp_path / "en.aiff", tmp_path / "en.wav"
    subprocess.run(["say", "-v", "Samantha", "-o", aiff, "Hello, this is a short test."], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", aiff, "-ac", "1", "-ar", "16000", wav], check=True)
    subprocess.run([apple_asr, "--timeout", "60", "en-US", wav, tmp_path / "o.json"], check=True)
    assert "short test" in (tmp_path / "o.json").read_text(encoding="utf-8")
