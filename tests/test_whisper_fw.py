"""lib/whisper_fw.py (whisper on Linux/Windows) and the faster-whisper branch of lib/lid.py, with a stub
faster_whisper module: the mlx_whisper command line in, mlx_whisper's JSON out."""
import json
import sys
import types
import wave
from dataclasses import dataclass

import numpy as np
import pytest

# mlx_whisper's JSON (mlx_whisper/transcribe.py: new_segment + "id"; the result dict)
MLX_KEYS = {"text", "segments", "language"}
MLX_SEGMENT_KEYS = {"id", "seek", "start", "end", "text", "tokens", "temperature", "avg_logprob", "compression_ratio",
                    "no_speech_prob"}


@dataclass
class Segment:  # faster_whisper.transcribe.Segment, the fields vidx uses
    id: int
    seek: int
    start: float
    end: float
    text: str
    tokens: list
    avg_logprob: float
    compression_ratio: float
    no_speech_prob: float
    words: object
    temperature: float


class StubModel:
    calls = []
    fail_on = None  # device whose model raises like a missing cuBLAS
    fail_running_on = None  # same, but only when the model runs: CTranslate2 loads cuBLAS on the first inference

    def __init__(self, model, device, compute_type, **kw):
        if device == StubModel.fail_on:
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        StubModel.calls.append(("load", model, device, compute_type))
        self.device = device
        self.model = types.SimpleNamespace(is_multilingual=True)

    def check_running(self):
        if self.device == StubModel.fail_running_on:
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

    def transcribe(self, audio, **kw):
        self.check_running()
        StubModel.calls.append(("transcribe", type(audio).__name__, str(audio.dtype), kw))
        segs = [Segment(0, 0, 0.0, 1.5, " Добрый вечер.", [50365, 1], -0.2, 1.1, 0.01, None, 0.0),
                Segment(1, 150, 1.5, 3.0, " Суп.", [50440, 2], -0.3, 1.0, 0.02, None, 0.0)]
        return iter(segs), types.SimpleNamespace(language=kw.get("language"), duration=3.0)

    def detect_language(self, audio, **kw):
        self.check_running()
        StubModel.calls.append(("detect", len(audio), kw))
        return "en", 0.9, [("en", 0.9), ("de", 0.06), ("ru", 0.04)]


@pytest.fixture
def stub(monkeypatch):
    StubModel.calls, StubModel.fail_on, StubModel.fail_running_on = [], None, None
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=StubModel))
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 0))
    return StubModel


def wav(path, seconds=3.0, sr=16000):
    x = (0.1 * np.sin(np.arange(int(sr * seconds)) / sr * 2 * np.pi * 220) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())
    return path


def run(argv, monkeypatch):
    import whisper_fw

    monkeypatch.setattr(sys, "argv", ["whisper_fw.py", *map(str, argv)])
    whisper_fw.main()


def test_mlx_command_line_and_json(tmp_path, stub, monkeypatch):
    audio = wav(tmp_path / "audio.wav")
    run([audio, "--model", "large-v3", "-o", tmp_path, "--output-name", "whisper-large-v3@ru", "-f", "json",
         "--verbose", "False", "--condition-on-previous-text", "False", "--language", "ru",
         "--initial-prompt", "Деплой", "--clip-timestamps", "0.0,1.5,2.0,3.0"], monkeypatch)
    out = json.loads((tmp_path / "whisper-large-v3@ru.json").read_text(encoding="utf-8"))
    assert set(out) == MLX_KEYS and out["language"] == "ru"
    assert [set(s) for s in out["segments"]] == [MLX_SEGMENT_KEYS] * 2
    assert [(s["start"], s["end"], s["text"]) for s in out["segments"]] == [(0.0, 1.5, " Добрый вечер."),
                                                                           (1.5, 3.0, " Суп.")]
    assert out["text"] == " Добрый вечер. Суп."
    (_, model, device, compute), (_, kind, dtype, kw) = stub.calls
    assert (model, device, compute) == ("large-v3", "cpu", "int8")
    assert (kind, dtype) == ("ndarray", "float32")  # decoded by vidx, not by PyAV (broken with av 19)
    assert kw == {"language": "ru", "initial_prompt": "Деплой", "condition_on_previous_text": False,
                  "clip_timestamps": "0.0,1.5,2.0,3.0", "beam_size": 1}


def test_defaults_whole_file_no_prompt(tmp_path, stub, monkeypatch):
    run([wav(tmp_path / "a.wav"), "--model", "tiny", "-o", tmp_path, "--verbose", "False"], monkeypatch)
    assert (tmp_path / "a.json").exists()  # stem of the audio, like mlx_whisper (vidx setup's warm-up)
    kw = stub.calls[1][3]
    assert kw["clip_timestamps"] == "0" and kw["initial_prompt"] is None and kw["language"] is None


def test_cuda_when_present(tmp_path, stub, monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))
    run([wav(tmp_path / "a.wav"), "--model", "tiny", "-o", tmp_path], monkeypatch)
    assert stub.calls[0][2:] == ("cuda", "float16")


def test_cuda_libraries_missing_falls_back_to_cpu(tmp_path, stub, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))
    stub.fail_on = "cuda"
    run([wav(tmp_path / "a.wav"), "--model", "tiny", "-o", tmp_path], monkeypatch)
    assert stub.calls[0][2:] == ("cpu", "int8")
    assert "falling back to CPU" in capsys.readouterr().err


def test_device_override(tmp_path, stub, monkeypatch):
    monkeypatch.setenv("VIDX_WHISPER_DEVICE", "cpu")
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))
    run([wav(tmp_path / "a.wav"), "--model", "tiny", "-o", tmp_path], monkeypatch)
    assert stub.calls[0][2:] == ("cpu", "int8")


def test_lid_uses_faster_whisper_without_mlx(stub, monkeypatch):
    import importlib.util

    import lid

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "mlx_whisper" else real(name, *a))
    detect = lid.load("large-v3-turbo")
    probs = detect(np.zeros(16000 * 8, dtype=np.float32))
    assert probs == {"en": 0.9, "de": 0.06, "ru": 0.04}
    assert stub.calls[0][1] == "large-v3-turbo" and stub.calls[-1][2] == {"language_detection_segments": 1}


def without_mlx(monkeypatch):
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "mlx_whisper" else real(name, *a))


@pytest.mark.parametrize("when", ["load", "inference"])
def test_lid_falls_back_to_cpu_when_cuda_libraries_are_missing(stub, monkeypatch, capsys, when):
    """Language ID had a CPU fallback only at load time, but CTranslate2 loads cuBLAS on the first inference: a
    machine with a GPU and no CUDA libraries failed language detection. It now falls back like whisper_fw."""
    import lid

    without_mlx(monkeypatch)
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))
    setattr(stub, "fail_on" if when == "load" else "fail_running_on", "cuda")
    detect = lid.load("large-v3-turbo")
    assert detect(np.zeros(16000, dtype=np.float32)) == {"en": 0.9, "de": 0.06, "ru": 0.04}
    assert detect(np.zeros(16000, dtype=np.float32))["en"] == 0.9  # stays on the CPU
    assert [c[2:] for c in stub.calls if c[0] == "load"][-1] == ("cpu", "int8")
    assert [c[0] for c in stub.calls].count("load") == (1 if when == "load" else 2)
    assert "falling back to CPU" in capsys.readouterr().err


def test_whisper_falls_back_when_cuda_fails_while_transcribing(tmp_path, stub, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))
    stub.fail_running_on = "cuda"
    run([wav(tmp_path / "a.wav"), "--model", "tiny", "-o", tmp_path], monkeypatch)
    assert [c[2:] for c in stub.calls if c[0] == "load"] == [("cuda", "float16"), ("cpu", "int8")]
    assert (tmp_path / "a.json").exists() and "falling back to CPU" in capsys.readouterr().err


@pytest.mark.parametrize("missing, want", [
    ((), "cuda float16"),
    (("cublas64_12.dll",), "cpu int8 (CUDA libraries missing: cublas64_12.dll)"),
])
def test_device_report_names_the_device_whisper_really_uses(monkeypatch, missing, want):
    """vidx setup said "cuda float16" whenever CTranslate2 saw a GPU, also when the CUDA libraries were missing and
    every run fell back to the CPU. The probe now loads them as CTranslate2 will, and says what happens."""
    import device

    monkeypatch.delenv("VIDX_WHISPER_DEVICE", raising=False)
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))

    def load(name):
        if name in missing:
            raise OSError(f"{name}: not found")

    monkeypatch.setattr(device, "load_library", load)
    monkeypatch.setattr(device, "CUDA_LIBRARIES", ["cublas64_12.dll"])
    assert device.device("whisper") == want


def test_device_report_without_a_gpu_loads_no_cuda_library(monkeypatch):
    import device

    monkeypatch.delenv("VIDX_WHISPER_DEVICE", raising=False)
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 0))
    monkeypatch.setattr(device, "load_library", lambda name: pytest.fail("no GPU: nothing to load"))
    assert device.device("whisper") == "cpu int8"


def test_cuda_library_names_per_platform():
    import device

    assert device.cuda_libraries("win32") == ["cublas64_12.dll"]
    assert device.cuda_libraries("linux") == ["libcublas.so.12"]


def test_a_prompt_starting_with_a_dash(tmp_path, stub, monkeypatch):
    """vidx passes the prompt as --initial-prompt=<prompt>, which argparse (here and in mlx_whisper) takes whole
    even when the prompt starts with "-"; as a separate argument it would be read as an unknown option."""
    run([wav(tmp_path / "a.wav"), "--model", "tiny", "-o", tmp_path, "--initial-prompt=-Kubernetes"], monkeypatch)
    assert stub.calls[1][3]["initial_prompt"] == "-Kubernetes"
    with pytest.raises(SystemExit):
        run([wav(tmp_path / "a.wav"), "--model", "tiny", "--initial-prompt", "-Kubernetes"], monkeypatch)
