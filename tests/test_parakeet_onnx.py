"""lib/parakeet_onnx.py (parakeet on Linux/Windows) with stub onnx_asr / onnxruntime modules: parakeet-mlx's
command line in, parakeet-mlx's JSON (sentences with token times) out."""
import json
import sys
import types
import wave

import numpy as np
import pytest

# parakeet-mlx's JSON (parakeet_mlx/cli.py: to_json, _aligned_sentence_to_dict, _aligned_token_to_dict)
MLX_KEYS = {"text", "sentences"}
SENTENCE_KEYS = {"text", "start", "end", "duration", "confidence", "tokens"}
TOKEN_KEYS = {"text", "start", "end", "duration", "confidence"}


class Seg:  # onnx_asr's TimestampedSegmentResult: times of tokens are relative to the segment start
    def __init__(self, start, end, tokens, timestamps, logprobs=None):
        self.start, self.end, self.tokens, self.timestamps, self.logprobs = start, end, tokens, timestamps, logprobs
        self.text = "".join(tokens).strip()


class Stub:
    calls = []
    providers = ["CPUExecutionProvider"]
    segments = []

    def __init__(self):
        Stub.calls = []

    @staticmethod
    def load_model(name, quantization=None, providers=None):
        Stub.calls.append(("model", name, quantization, providers))
        return Stub.Model()

    @staticmethod
    def load_vad(name, **kw):
        Stub.calls.append(("vad", name))
        return "vad"

    class Model:
        def with_vad(self, vad, **kw):
            Stub.calls.append(("with_vad", vad))
            return self

        def with_timestamps(self):
            return self

        def recognize(self, audio, sample_rate=16000):
            Stub.calls.append(("recognize", type(audio).__name__, sample_rate))
            return iter(Stub.segments)


@pytest.fixture
def stub(monkeypatch):
    Stub.providers = ["CPUExecutionProvider"]
    Stub.segments = [
        Seg(1.0, 4.2, [" Добрый", " вечер", ".", " Сегодня", " суп", "."], [0.0, 0.4, 0.9, 1.2, 1.8, 2.4],
            [-0.1, -0.2, -0.05, -0.3, -0.1, -0.02]),
        Seg(6.0, 7.5, [" Hello", " there"], [0.1, 0.6]),  # no final punctuation, no log probabilities
    ]
    monkeypatch.setitem(sys.modules, "onnx_asr", Stub)
    monkeypatch.setitem(sys.modules, "onnxruntime",
                        types.SimpleNamespace(get_available_providers=lambda: list(Stub.providers)))
    return Stub()


def wav(path, seconds=8.0, sr=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.zeros(int(sr * seconds), dtype=np.int16).tobytes())
    return path


def run(argv, monkeypatch):
    import parakeet_onnx

    monkeypatch.setattr(sys, "argv", ["parakeet_onnx.py", *map(str, argv)])
    parakeet_onnx.main()


def test_mlx_command_line_and_json(tmp_path, stub, monkeypatch):
    run([wav(tmp_path / "audio.wav"), "--model", "nemo-parakeet-tdt-0.6b-v3", "--output-format", "json",
         "--output-dir", tmp_path / "asr", "--output-template", "parakeet"], monkeypatch)
    out = json.loads((tmp_path / "asr" / "parakeet.json").read_text(encoding="utf-8"))
    assert set(out) == MLX_KEYS
    assert all(set(s) == SENTENCE_KEYS and all(set(t) == TOKEN_KEYS for t in s["tokens"]) for s in out["sentences"])
    got = [(s["text"], s["start"], s["end"]) for s in out["sentences"]]
    assert got == [("Добрый вечер.", 1.0, 2.2), ("Сегодня суп.", 2.2, 4.2), ("Hello there", 6.1, 7.5)]
    assert out["text"] == "Добрый вечер. Сегодня суп. Hello there"
    first = out["sentences"][0]
    assert first["duration"] == 1.2 and 0 < first["confidence"] <= 1
    assert [t["start"] for t in first["tokens"]] == [1.0, 1.4, 1.9]  # absolute times
    assert out["sentences"][2]["confidence"] == 1.0  # no log probabilities: certain, like a missing value
    assert ("model", "nemo-parakeet-tdt-0.6b-v3", "int8", ["CPUExecutionProvider"]) in stub.calls
    assert ("recognize", "ndarray", 16000) in stub.calls and ("vad", "silero") in stub.calls


@pytest.mark.parametrize("available, want, quant", [
    (["CUDAExecutionProvider", "CPUExecutionProvider"], ["CUDAExecutionProvider", "CPUExecutionProvider"], None),
    (["DmlExecutionProvider", "CPUExecutionProvider"], ["DmlExecutionProvider", "CPUExecutionProvider"], None),
    (["AzureExecutionProvider", "CPUExecutionProvider"], ["CPUExecutionProvider"], "int8"),
    (["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"],
     ["CUDAExecutionProvider", "CPUExecutionProvider"], None),
])
def test_providers_gpu_first_int8_only_on_cpu(tmp_path, stub, monkeypatch, available, want, quant):
    Stub.providers = available
    run([wav(tmp_path / "a.wav"), "--model", "m", "--output-dir", tmp_path], monkeypatch)
    assert ("model", "m", quant, want) in stub.calls
    assert (tmp_path / "a.json").exists()  # default template: the audio's stem, like parakeet-mlx


def test_provider_override(tmp_path, stub, monkeypatch):
    Stub.providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    monkeypatch.setenv("VIDX_ONNX_PROVIDER", "CPUExecutionProvider")
    run([wav(tmp_path / "a.wav"), "--model", "m", "--output-dir", tmp_path], monkeypatch)
    assert ("model", "m", "int8", ["CPUExecutionProvider"]) in stub.calls


def test_merge_reads_it(tmp_path, stub, monkeypatch):
    """common.load_segments (merge, find, regions snapping) reads the sentences."""
    import common

    run([wav(tmp_path / "a.wav"), "--model", "m", "--output-dir", tmp_path, "--output-template", "parakeet"],
        monkeypatch)
    assert common.load_segments(tmp_path / "parakeet.json")[0] == (1.0, 2.2, "Добрый вечер.")
