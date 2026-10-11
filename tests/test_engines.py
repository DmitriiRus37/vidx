"""The engine registry (vidx.engines): one program per engine and platform, the same arguments everywhere."""
import os
from pathlib import PureWindowsPath

import pytest
from helpers import MEDIA, fake_calls, needs_ffmpeg, run_vidx

from vidx.engines import Registry


def test_darwin_runs_mlx_and_the_swift_helpers():
    reg = Registry("darwin", "/h")
    assert reg.get("whisper").program == ["mlx_whisper"] and reg.get("whisper").model.startswith("mlx-community/")
    assert reg.get("turbo").model == "mlx-community/whisper-large-v3-turbo"
    assert reg.get("parakeet").program == ["parakeet-mlx"]
    assert reg.get("apple").program == [os.path.join("/h", "bin", "apple_asr")]
    assert reg.get("ocr").program == [os.path.join("/h", "bin", "ocr")]
    assert reg.lid.model == "mlx-community/whisper-large-v3-turbo"


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_linux_and_windows_run_the_portable_backends(platform):
    reg = Registry(platform, "/h")
    assert reg.get("whisper").program[1].endswith("whisper_fw.py") and reg.get("whisper").model == "large-v3"
    assert reg.get("turbo").model == "large-v3-turbo"
    assert reg.get("parakeet").program[1].endswith("parakeet_onnx.py")
    assert reg.get("parakeet").model == "nemo-parakeet-tdt-0.6b-v3"
    assert reg.get("ocr").program[1].endswith("ocr_rapid.py")
    assert reg.get("apple") is None  # macOS only
    assert "mlx" not in str(reg.engines) and reg.lid.model == "large-v3-turbo"


def test_windows_interpreters():
    reg = Registry("win32", r"C:\vidx")
    want = PureWindowsPath(r"C:\vidx\.venv-whisper\Scripts\python.exe")
    assert PureWindowsPath(reg.get("whisper").program[0]) == want
    assert PureWindowsPath(reg.helper_python) == PureWindowsPath(r"C:\vidx\.venv\Scripts\python.exe")
    assert PureWindowsPath(reg.get("gigaam").program[0]) == PureWindowsPath(r"C:\vidx\.venv-giga\Scripts\python.exe")


def test_same_engines_and_models_shared():
    mac, lin = Registry("darwin", "/h"), Registry("linux", "/h")
    assert mac.get("gigaam").program == lin.get("gigaam").program  # one GigaAM script everywhere
    assert mac.get("vosk").program == lin.get("vosk").program
    assert mac.get("gigaam").model == "v3_e2e_rnnt"


FIXTURE = MEDIA / "ru_slides.mp4"


@needs_ffmpeg
@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_apple_not_available_is_not_a_failure(tmp_path, platform):
    r = run_vidx(FIXTURE, "-l", "de", "-o", "out", "-e", "apple,whisper", tmp_path=tmp_path,
                 env={"VIDX_FAKE_PLATFORM": platform, "VIDX_FAKE_APPLE_LOCALES": "de-DE"})
    assert r.returncode == 0, r.stderr
    assert "Apple SpeechTranscriber: not available on this platform, skipped" in r.stderr
    assert [c["engine"] for c in fake_calls(tmp_path, queries=True)] == ["whisper", "ocr"]


@needs_ffmpeg
@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_default_run_per_platform(tmp_path, platform):
    """Same queue, outputs and transcript on every platform; only the programs (and Apple) differ."""
    r = run_vidx(FIXTURE, "-l", "en", "-o", "out", tmp_path=tmp_path,
                 env={"VIDX_FAKE_PLATFORM": platform, "VIDX_FAKE_APPLE_LOCALES": "en-US"})
    assert r.returncode == 0, r.stderr
    engines = [c["engine"] for c in fake_calls(tmp_path)]
    apple = ["apple"] if platform == "darwin" else []
    assert engines == ["whisper", "vosk", "ocr", "parakeet", *apple, "turbo"]
    whisper = [c["args"] for c in fake_calls(tmp_path) if c["engine"] == "whisper"][0]
    model = "mlx-community/whisper-large-v3-mlx" if platform == "darwin" else "large-v3"
    assert whisper[1:3] == ["--model", model]
    out = tmp_path / "out"
    want = ["whisper-large-v3.json", "vosk.json", "parakeet.json", "whisper-turbo.json", *(f"{a}.json" for a in apple)]
    assert sorted(p.name for p in (out / "asr").iterdir()) == sorted(want)
    assert (out / "transcript.txt").read_text(encoding="utf-8") == "[00:00:00] Fake whisper en.\n"


def test_rapidocr_models_live_in_home():
    reg = Registry("linux", "/h")
    assert reg.get("ocr").env == {"VIDX_RAPIDOCR_MODELS": os.path.join("/h", "models", "rapidocr")}
    assert Registry("darwin", "/h").get("ocr").env == {}


@needs_ffmpeg
@pytest.mark.parametrize("platform, header", [("darwin", "# On-screen text (Apple Vision OCR)"),
                                              ("linux", "# On-screen text (RapidOCR)")])
def test_ocr_md_names_the_backend(tmp_path, platform, header):
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", "-e", "whisper", tmp_path=tmp_path,
                 env={"VIDX_FAKE_PLATFORM": platform})
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out" / "ocr.md").read_text(encoding="utf-8").splitlines()[0] == header


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_model_overrides(platform):
    """Smaller models for small machines and smoke runs; language ID follows turbo."""
    env = {"VIDX_MODEL_WHISPER": "tiny", "VIDX_MODEL_TURBO": "base", "VIDX_MODEL_PARAKEET": "my-parakeet"}
    reg = Registry(platform, "/h", env=env)
    assert (reg.get("whisper").model, reg.get("turbo").model, reg.get("parakeet").model) == ("tiny", "base",
                                                                                             "my-parakeet")
    assert reg.lid.model == "base"


@needs_ffmpeg
def test_vosk_model_override(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", "-e", "vosk", tmp_path=tmp_path,
                 env={"VIDX_VOSK_RU": "vosk-model-small-ru-0.22"})
    assert r.returncode == 0, r.stderr
    (call,) = [c for c in fake_calls(tmp_path) if c["engine"] == "vosk"]
    assert call["args"][0].endswith("vosk-model-small-ru-0.22")
    assert (tmp_path / "out" / ".vidx" / "vosk.json.params").read_text(encoding="utf-8") == "vosk-model-small-ru-0.22"


@needs_ffmpeg
def test_whisper_model_override_in_a_run(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", "-e", "whisper", tmp_path=tmp_path,
                 env={"VIDX_MODEL_WHISPER": "tiny"})
    assert r.returncode == 0, r.stderr
    (call,) = [c for c in fake_calls(tmp_path) if c["engine"] == "whisper"]
    assert call["args"][1:3] == ["--model", "tiny"]
