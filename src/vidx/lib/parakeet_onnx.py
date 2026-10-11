"""parakeet on Linux and Windows: onnx-asr's nemo-parakeet-tdt-0.6b-v3 behind parakeet-mlx's command line and
JSON, so vidx, merge.py and the region snapping treat it exactly like the macOS backend.

usage: parakeet_onnx.py <audio.wav 16k mono> --model M [--output-format json] [--output-dir DIR]
                        [--output-template NAME]
out: DIR/NAME.json = {"text", "sentences": [{text, start, end, duration, confidence,
     tokens: [{text, start, end, duration, confidence}]}]} — parakeet-mlx's keys.
The model takes 20-30 s at a time, so Silero VAD cuts the audio at pauses first; each VAD segment is split
into sentences after tokens ending in . ? ! … (token times from the model, made absolute). A token lasts until
the next one starts (the last one until the segment ends); confidence = exp(mean token log probability).
Execution provider: CUDA, then DirectML (Windows), then CPU (int8 weights there: smaller and faster);
VIDX_ONNX_PROVIDER=<name>ExecutionProvider forces one.
"""
import argparse
import json
import math
import os
import sys

from common import read_wav

GPU = ("CUDAExecutionProvider", "DmlExecutionProvider")
SENTENCE_END = (".", "?", "!", "…", "。", "？", "！")


def args():
    ap = argparse.ArgumentParser(description="parakeet via onnx-asr, parakeet-mlx's command line")
    ap.add_argument("audio")
    ap.add_argument("--model", required=True)
    ap.add_argument("--output-format", default="json", choices=["json", "all"])
    ap.add_argument("--output-dir", default=".")
    ap.add_argument("--output-template")
    return ap.parse_args()


def providers():
    """onnxruntime providers to use, best first; always ending with the CPU."""
    import onnxruntime

    forced = os.environ.get("VIDX_ONNX_PROVIDER")
    if forced:
        return [forced] + ([] if forced == "CPUExecutionProvider" else ["CPUExecutionProvider"])
    available = onnxruntime.get_available_providers()
    best = next((p for p in GPU if p in available), None)
    return ([best] if best else []) + ["CPUExecutionProvider"]


def r3(x):
    return round(float(x), 3)


def sentences(seg):
    """One VAD segment → parakeet-mlx sentences with absolute times."""
    tokens = list(getattr(seg, "tokens", None) or [])
    times = [seg.start + t for t in (getattr(seg, "timestamps", None) or [])]
    if not tokens or len(times) != len(tokens):
        return [sentence([(seg.text, seg.start, seg.end, None)])] if seg.text.strip() else []
    logprobs = list(getattr(seg, "logprobs", None) or []) or [None] * len(tokens)
    ends = times[1:] + [seg.end]
    res, cur = [], []
    for tok, a, b, lp in zip(tokens, times, ends, logprobs, strict=True):
        cur.append((tok, a, b, lp))
        if tok.strip().endswith(SENTENCE_END):
            res.append(sentence(cur))
            cur = []
    if cur:
        res.append(sentence(cur))
    return [s for s in res if s["text"]]


def sentence(toks):
    lps = [lp for *_, lp in toks if lp is not None]
    conf = math.exp(sum(lps) / len(lps)) if lps else 1.0
    start, end = toks[0][1], toks[-1][2]
    return {"text": "".join(t for t, *_ in toks).strip(), "start": r3(start), "end": r3(end),
            "duration": r3(end - start), "confidence": r3(conf),
            "tokens": [{"text": t, "start": r3(a), "end": r3(b), "duration": r3(b - a),
                        "confidence": r3(math.exp(lp) if lp is not None else 1.0)} for t, a, b, lp in toks]}


def main():
    a = args()
    import onnx_asr

    sr, x = read_wav(a.audio)
    use = providers()
    model = onnx_asr.load_model(a.model, quantization="int8" if use[0] == "CPUExecutionProvider" else None,
                                providers=use)
    vad = onnx_asr.load_vad("silero")
    sents = []
    for seg in model.with_vad(vad).with_timestamps().recognize(x, sample_rate=sr):
        sents += sentences(seg)
        print(f"\r  parakeet {seg.end:.0f}/{len(x) / sr:.0f} s", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    name = a.output_template or os.path.splitext(os.path.basename(a.audio))[0]
    os.makedirs(a.output_dir, exist_ok=True)
    with open(os.path.join(a.output_dir, name + ".json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"text": " ".join(s["text"] for s in sents), "sentences": sents}, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
