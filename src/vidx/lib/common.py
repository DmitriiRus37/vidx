"""Helpers shared by the vidx scripts. Runs in all three interpreters (.venv, .venv-giga and
mlx-whisper's own Python); numpy is imported only where it is needed."""
import json
import sys

SILENCE_RMS = 1e-3  # float32 RMS below which a clip is treated as silence


def hms(t, sep=":"):
    t = int(t)
    return f"{t // 3600:02d}{sep}{t % 3600 // 60:02d}{sep}{t % 60:02d}"


def load_segments(path):
    """Engine output → [(start, end, text)]. Accepts {"segments": [...]}, parakeet's
    {"sentences": [...]} and a bare list."""
    with open(path, encoding="utf-8") as f:
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


FRAME_S = 0.03  # loudness frame of the pause cutters


def frame_rms(x, sr):
    """Loudness of every whole FRAME_S frame of x: RMS (+1e-12 under the root, so silence is not 0)."""
    import numpy as np

    fr = int(FRAME_S * sr)
    n = len(x) // fr
    return np.sqrt((x[:n * fr].reshape(n, fr) ** 2).mean(1) + 1e-12)


def top(probs):
    """The most probable key of {language: probability}."""
    return max(probs, key=probs.get)


def pause_cuts(x, sr, lo_s, hi_s):
    """Cut audio at pauses: measure the loudness of every FRAME_S frame, cut at the quietest frame
    lo_s..hi_s after the previous cut, repeat. → [(start, end)] sample ranges covering all of x.
    Used by GigaAM (14-22 s chunks) and the language regions (2-8 s pieces)."""
    import numpy as np

    fr = int(FRAME_S * sr)
    e = frame_rms(x, sr)
    n = len(e)
    pts, i = [0], 0
    while True:
        lo, hi = i + int(lo_s / FRAME_S), i + int(hi_s / FRAME_S)
        if hi >= n:
            pts.append(n)
            break
        j = lo + int(np.argmin(e[lo:hi]))
        pts.append(j)
        i = j
    res = [(a * fr, b * fr) for a, b in zip(pts, pts[1:], strict=False)]
    if res:
        res[-1] = (res[-1][0], len(x))  # the tail after the last whole frame
    return res


def unix_stdout(stream=None):
    """Print data with LF line ends on every OS: a script whose stdout vidx saves to a file (lang.txt,
    ocr.jsonl) would write CRLF on Windows."""
    (stream or sys.stdout).reconfigure(newline="\n")


def dump_json(obj, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
