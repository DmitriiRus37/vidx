"""Vosk (Kaldi, CPU). usage: vosk_run.py <model_dir> <audio.wav 16k mono> <out.json>"""
import json
import sys
import wave

from vosk import KaldiRecognizer, Model, SetLogLevel

SetLogLevel(-1)
model_dir, src, out = sys.argv[1:4]
segs = []


def take(res):
    r = json.loads(res)
    words = r.get("result") or []
    if words and r.get("text"):
        segs.append({"start": words[0]["start"], "end": words[-1]["end"], "text": r["text"]})


with wave.open(src) as w:  # fed in 0.5 s pieces
    rec = KaldiRecognizer(Model(model_dir), w.getframerate())
    rec.SetWords(True)
    total, done = w.getnframes(), 0
    while data := w.readframes(8000):
        done += len(data) // 2
        if rec.AcceptWaveform(data):
            take(rec.Result())
        print(f"\r  vosk {100 * done // max(total, 1)}%", end="", file=sys.stderr, flush=True)
    take(rec.FinalResult())
print(file=sys.stderr)
with open(out, "w", encoding="utf-8", newline="\n") as f:
    json.dump({"engine": "vosk", "segments": segs}, f, ensure_ascii=False, indent=1)
