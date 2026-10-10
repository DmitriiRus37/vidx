"""Helpers shared by the vidx scripts. Runs in all three interpreters (.venv, .venv-giga and
mlx-whisper's own Python); numpy is imported only where it is needed."""
import json

SILENCE_RMS = 1e-3  # float32 RMS below which a clip is treated as silence


def hms(t, sep=":"):
    t = int(t)
    return f"{t // 3600:02d}{sep}{t % 3600 // 60:02d}{sep}{t % 60:02d}"


def load_segments(path):
    """Engine output → [(start, end, text)]. Accepts {"segments": [...]}, parakeet's
    {"sentences": [...]} and a bare list."""
    with open(path) as f:
        d = json.load(f)
    items = (d.get("segments") or d.get("sentences") or []) if isinstance(d, dict) else d
    return [(float(s["start"]), float(s["end"]), s["text"].strip())
            for s in items if s.get("text", "").strip()]


def read_wav(path):
    """16-bit mono WAV → (sample rate, float32 samples in -1..1)."""
    import wave

    import numpy as np

    with wave.open(path) as w:
        sr, raw = w.getframerate(), w.readframes(w.getnframes())
    return sr, np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768


def is_silent(clip):
    import numpy as np

    return len(clip) == 0 or float(np.sqrt((clip ** 2).mean())) < SILENCE_RMS


def dump_json(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
