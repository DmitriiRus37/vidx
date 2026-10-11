"""Language regions with the real language ID (marker real: whisper turbo via mlx-whisper, cached)."""
import json
import os
import subprocess

import pytest
from helpers import FIXTURES, LIB, MEDIA

pytestmark = pytest.mark.real


def wav(src, tmp_path):
    out = tmp_path / "audio.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", out], check=True)
    return out


def run_regions(lid_backend, media, langs, tmp_path, parakeet=None):
    python, model = lid_backend
    out = tmp_path / "regions.json"
    args = [python, str(LIB / "regions.py"), model, langs, wav(MEDIA / media, tmp_path), out]
    if parakeet:
        args.append(FIXTURES / "regions" / parakeet)
    r = subprocess.run(args, capture_output=True, text=True, env={**os.environ, "HF_HUB_OFFLINE": "1"})
    assert r.returncode == 0, r.stderr
    with open(out) as f:
        return [(x["lang"], x["start"], x["end"]) for x in json.load(f)], r.stderr


def boundaries(regions):
    return [end for _, _, end in regions[:-1]]


@pytest.mark.parametrize("parakeet, tol", [(None, 1.0), ("mix3_parakeet.json", 0.5)])
def test_mix3_three_languages(lid_backend, tmp_path, parakeet, tol):
    regions, _ = run_regions(lid_backend, "mix3.m4a", "uk,en,de", tmp_path, parakeet)
    assert [r[0] for r in regions] == ["uk", "en", "de"]
    assert boundaries(regions) == [pytest.approx(6.14, abs=tol), pytest.approx(11.28, abs=tol)]


@pytest.mark.parametrize("parakeet, tol", [(None, 1.0), ("ru_en_parakeet.json", 0.5)])
def test_ru_en_same_as_before(lid_backend, tmp_path, parakeet, tol):
    """Regions of the parakeet-script split (0-17.84 ru, 17.84-30.4 en, ru after), within tol."""
    regions, _ = run_regions(lid_backend, "ru_en.m4a", "ru,en", tmp_path, parakeet)
    assert [r[0] for r in regions] == ["ru", "en", "ru"]
    assert boundaries(regions) == [pytest.approx(17.84, abs=tol), pytest.approx(30.4, abs=tol)]


@pytest.mark.parametrize("media, langs, switch", [("ru_uk.m4a", "ru,uk", 5.37), ("de_fr.m4a", "de,fr", 5.33)])
def test_same_script_pairs(lid_backend, tmp_path, media, langs, switch):
    """Milena (ru) then Lesya (uk); Anna (de) then Thomas (fr): the script cannot separate them, the audio can."""
    regions, err = run_regions(lid_backend, media, langs, tmp_path)
    assert [r[0] for r in regions] == langs.split(",")
    assert boundaries(regions) == [pytest.approx(switch, abs=1.0)]
    assert "could not be separated" not in err
