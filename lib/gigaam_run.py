"""GigaAM-v3 on long audio without pyannote: cut at the quietest 30 ms frame 14-22 s into each
chunk, skip silent chunks, transcribe each one (GigaAM refuses audio longer than 25 s).

usage: gigaam_run.py <audio.wav 16k mono> <out.json> [regions.json]
regions.json (mixed-language runs): only the "ru" regions are transcribed, cut separately, so no
chunk crosses a language switch (GigaAM turns English speech into Latin-letter gibberish).
out.json: {"engine": "gigaam", "segments": [{"start", "end", "text"}]}
"""
import itertools
import json
import os
import sys
import tempfile

import numpy as np
import soundfile as sf


def cuts(x, sr=16000):
    fr = int(0.03 * sr)
    n = len(x) // fr
    e = np.sqrt((x[:n * fr].reshape(n, fr) ** 2).mean(1) + 1e-12)
    pts, i = [0], 0
    while True:
        lo, hi = i + int(14 / 0.03), i + int(22 / 0.03)
        if hi >= n:
            pts.append(n)
            break
        j = lo + int(np.argmin(e[lo:hi]))
        pts.append(j)
        i = j
    return [(a * fr, b * fr) for a, b in itertools.pairwise(pts)]


def main():
    src, out = sys.argv[1], sys.argv[2]
    name = "v3_e2e_rnnt"  # e2e = with punctuation
    import gigaam
    import torch

    model = gigaam.load_model(name, device="mps" if torch.backends.mps.is_available() else "cpu")
    x, sr = sf.read(src, dtype="float32")
    spans = [(0, len(x))]
    if len(sys.argv) > 3:
        with open(sys.argv[3]) as f:
            spans = [(int(r["start"] * sr), min(len(x), int(r["end"] * sr))) for r in json.load(f) if r["lang"] == "ru"]
    chunks = []
    for s0, s1 in spans:
        part = [(s0 + a, s0 + b) for a, b in cuts(x[s0:s1], sr)]
        if part:  # the tail after the last whole frame
            part[-1] = (part[-1][0], s1)
        chunks += part
    res, spoken, failed = [], 0, 0
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "c.wav")
        for k, (a, c) in enumerate(chunks):
            seg = x[a:c]
            print(f"\r  gigaam {k + 1}/{len(chunks)}", end="", file=sys.stderr, flush=True)
            if len(seg) < sr // 10 or np.sqrt((seg ** 2).mean()) < 1e-4:
                continue
            spoken += 1
            sf.write(tmp, seg, sr)
            try:
                t = model.transcribe(tmp).text.strip()
            except Exception as ex:  # noqa: BLE001 — one bad chunk (e.g. MPS out of memory) must not lose the rest
                print(f"\n  gigaam chunk {a / sr:.1f}s: {ex}", file=sys.stderr)
                failed += 1
                continue
            if t:
                res.append({"start": round(a / sr, 2), "end": round(c / sr, 2), "text": t})
    print(file=sys.stderr)
    if spoken and failed == spoken:
        sys.exit(f"gigaam: all {failed} chunks failed")  # no empty result that would be cached as done
    with open(out, "w") as f:
        json.dump({"engine": "gigaam", "segments": res}, f, ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
