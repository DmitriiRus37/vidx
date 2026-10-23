"""The engine runners' own logic with their model libraries faked (gigaam, soundfile, torch, faster-whisper):
chunking, silent and failed chunks, regions, device reports, language-ID loading. No models, no network."""
import json
import sys
import types

import device
import find
import gigaam_run
import lid
import numpy as np
import pytest
import whisper_fw
from helpers import out_dir_from

SR = 16000


def tone(seconds, amp=0.3):
    return (amp * np.sin(np.arange(int(SR * seconds)) / SR * 2 * np.pi * 300)).astype(np.float32)


def fake_gigaam(monkeypatch, audio, transcribe):
    """sys.modules gigaam / soundfile / torch for gigaam_run.main; returns the list of load_model calls."""
    loads = []
    model = types.SimpleNamespace(transcribe=transcribe)
    monkeypatch.setitem(sys.modules, "gigaam", types.SimpleNamespace(
        load_model=lambda name, device: loads.append((name, device)) or model))
    monkeypatch.setitem(sys.modules, "soundfile", types.SimpleNamespace(
        read=lambda path, dtype: (audio, SR), write=lambda path, seg, sr: np.save(path + ".npy", seg)))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False), backends=types.SimpleNamespace()))
    monkeypatch.delenv("VIDX_GIGAAM_DEVICE", raising=False)
    return loads


def run_gigaam(monkeypatch, tmp_path, *extra):
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["gigaam_run.py", str(tmp_path / "a.wav"), str(out), *map(str, extra)])
    gigaam_run.main()
    return json.loads(out.read_text(encoding="utf-8"))


def test_gigaam_transcribes_spoken_chunks_and_skips_silence(tmp_path, monkeypatch):
    audio = np.concatenate([tone(20), np.zeros(SR * 20, np.float32), tone(20)])
    seen = []

    def transcribe(path):
        seen.append(path)
        return types.SimpleNamespace(text=f"  chunk {len(seen)} ")

    loads = fake_gigaam(monkeypatch, audio, transcribe)
    got = run_gigaam(monkeypatch, tmp_path)
    assert loads == [("v3_e2e_rnnt", "cpu")]
    assert got["engine"] == "gigaam"
    segs = got["segments"]
    assert [s["text"] for s in segs] == [f"chunk {k + 1}" for k in range(len(segs))]
    assert len(segs) == len(seen) >= 2
    assert segs[0]["start"] == 0 and segs[-1]["end"] == 60
    assert all(not (s["start"] >= 20 and s["end"] <= 40) for s in segs)  # the silent middle is never sent


def test_gigaam_regions_keep_only_russian(tmp_path, monkeypatch):
    audio = tone(30)
    regions = tmp_path / "regions.json"
    regions.write_text(json.dumps([{"start": 0, "end": 10, "lang": "en"}, {"start": 10, "end": 30, "lang": "ru"}]),
                       encoding="utf-8", newline="\n")
    fake_gigaam(monkeypatch, audio, lambda path: types.SimpleNamespace(text="привет"))
    segs = run_gigaam(monkeypatch, tmp_path, regions)["segments"]
    assert segs and segs[0]["start"] == 10 and segs[-1]["end"] == 30
    assert all(s["text"] == "привет" for s in segs)


def test_gigaam_one_failed_chunk_does_not_lose_the_rest(tmp_path, monkeypatch, capsys):
    calls = []

    def transcribe(path):
        calls.append(path)
        if len(calls) == 1:
            raise RuntimeError("MPS backend out of memory")
        return types.SimpleNamespace(text="ok")

    fake_gigaam(monkeypatch, tone(40), transcribe)
    segs = run_gigaam(monkeypatch, tmp_path)["segments"]
    assert len(segs) == len(calls) - 1 and all(s["text"] == "ok" for s in segs)
    assert "MPS backend out of memory" in capsys.readouterr().err


def test_gigaam_empty_texts_are_dropped(tmp_path, monkeypatch):
    fake_gigaam(monkeypatch, tone(10), lambda path: types.SimpleNamespace(text="   "))
    assert run_gigaam(monkeypatch, tmp_path)["segments"] == []


def test_gigaam_all_chunks_failed_writes_nothing(tmp_path, monkeypatch):
    def transcribe(path):
        raise RuntimeError("boom")

    fake_gigaam(monkeypatch, tone(10), transcribe)
    with pytest.raises(SystemExit, match="gigaam: all 1 chunks failed"):
        run_gigaam(monkeypatch, tmp_path)
    assert not (tmp_path / "out.json").exists()  # no empty result that would be cached as done


# --- device.py (vidx setup's device report) --------------------------------------------------------------------

def test_device_parakeet_reports_the_first_provider(monkeypatch):
    import parakeet_onnx

    monkeypatch.setattr(parakeet_onnx, "providers", lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    assert device.device("parakeet") == "CUDAExecutionProvider"


def test_device_gigaam_asks_torch(monkeypatch):
    torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True),
                                  backends=types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.delenv("VIDX_GIGAAM_DEVICE", raising=False)
    assert device.device("gigaam") == "cuda"


def test_device_unknown_engine():
    with pytest.raises(SystemExit, match="unknown engine 'vosk'"):
        device.device("vosk")


@pytest.mark.parametrize("platform, cdll_kwargs", [("linux", {}), ("win32", {"winmode": 0})])
def test_load_library_like_ctranslate2(platform, cdll_kwargs, monkeypatch):
    import ctypes

    calls = []
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(ctypes, "CDLL", lambda name, **kw: calls.append((name, kw)) or "handle")
    assert device.load_library("libcublas.so.12") == "handle"
    assert calls == [("libcublas.so.12", cdll_kwargs)]


# --- lid.py with faster-whisper (Linux, Windows) ---------------------------------------------------------------

def test_lid_faster_whisper_loads_once_and_detects(monkeypatch):
    runs = []

    class FakeWhisper:
        def detect_language(self, clip, language_detection_segments):
            assert language_detection_segments == 1
            return "en", 0.9, [("en", 0.9), ("de", 0.1)]

    class FakeModel:
        def __init__(self, model_id):
            self.model_id = model_id

        def run(self, fn):
            runs.append(self.model_id)
            return fn(FakeWhisper())

    monkeypatch.setattr(whisper_fw, "Model", FakeModel)
    detect = lid.load_faster_whisper("large-v3-turbo")
    assert runs == ["large-v3-turbo"]  # loaded right away (vidx setup caches it)
    assert detect(np.zeros(SR, np.float32)) == {"en": 0.9, "de": 0.1}


def test_lid_picks_faster_whisper_without_mlx(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(lid, "load_faster_whisper", lambda model: ("fw", model))
    assert lid.load("large-v3-turbo") == ("fw", "large-v3-turbo")


# --- find.py: OCR hits -----------------------------------------------------------------------------------------

def test_find_reports_ocr_lines_at_their_frame_time(tmp_path, monkeypatch, capsys):
    out = out_dir_from("merge/ru", tmp_path)
    (out / "frames").mkdir()
    (out / "ocr").mkdir()
    (out / "frames" / "frames.json").write_text(json.dumps([{"file": "b.jpg", "t": 75}, {"file": "a.jpg", "t": 5}]),
                                                encoding="utf-8", newline="\n")
    rows = [{"file": "b.jpg", "text": "Рецепт супа\nморковь"}, {"file": "a.jpg", "text": "Суп дня"}]
    (out / "ocr" / "ocr.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                           encoding="utf-8", newline="\n")
    monkeypatch.setattr(sys, "argv", ["find.py", str(out), "суп", "ocr"])
    find.main()
    got = capsys.readouterr().out.splitlines()
    assert got == ["00:00:05  ocr               Суп дня   (frames/unique/a.jpg)",
                   "00:01:15  ocr               Рецепт супа   (frames/unique/b.jpg)"]
