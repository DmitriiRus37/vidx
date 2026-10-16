"""whisper on Linux and Windows: faster-whisper (CTranslate2) behind mlx_whisper's command line and JSON, so
vidx and merge.py treat it exactly like the macOS backend.

usage: whisper_fw.py <audio.wav 16k mono> --model M [-o DIR] [--output-name STEM] [-f json] [--verbose False]
                     [--condition-on-previous-text True|False] [--language L] [--initial-prompt P]
                     [--clip-timestamps "start,end,start,end"]
  M          a faster-whisper model: large-v3, large-v3-turbo, tiny... (Hugging Face cache) or a local path
out: DIR/STEM.json = {"text", "segments": [{id, seek, start, end, text, tokens, temperature, avg_logprob,
     compression_ratio, no_speech_prob}], "language"} — mlx_whisper's keys, written the same way.
Device: CUDA (float16) when CTranslate2 sees a GPU, else CPU (int8); VIDX_WHISPER_DEVICE=cpu|cuda forces one.
If the CUDA libraries cannot be loaded (cuBLAS missing on Windows), it says so and runs on the CPU.
Decoding is greedy (beam size 1), like mlx_whisper. The audio is read here (16-bit WAV, as vidx writes it),
not by PyAV: faster-whisper 1.2.1's decoder fails with PyAV 19.
"""
import argparse
import json
import os
import sys

from common import read_wav, unix_stderr


def args():
    ap = argparse.ArgumentParser(description="whisper via faster-whisper, mlx_whisper's command line")
    ap.add_argument("audio")
    ap.add_argument("--model", required=True)
    ap.add_argument("-o", "--output-dir", default=".")
    ap.add_argument("--output-name")
    ap.add_argument("-f", "--output-format", default="json", choices=["json", "all"])
    ap.add_argument("--verbose", default="False")
    ap.add_argument("--condition-on-previous-text", default="True", choices=["True", "False"])
    ap.add_argument("--language")
    ap.add_argument("--initial-prompt")
    ap.add_argument("--clip-timestamps", default="0")
    return ap.parse_args()


def devices():
    """[(device, compute type)] to try, best first."""
    forced = os.environ.get("VIDX_WHISPER_DEVICE")
    if forced:
        return [(forced, "float16" if forced == "cuda" else "int8")]
    import ctranslate2

    cuda = ctranslate2.get_cuda_device_count() > 0
    return ([("cuda", "float16")] if cuda else []) + [("cpu", "int8")]


def run_model(model_id, run):
    """run(model) on the best device; a CUDA failure (missing libraries) falls back to the CPU."""
    from faster_whisper import WhisperModel

    options = devices()
    for i, (device, compute) in enumerate(options):
        try:
            return run(WhisperModel(model_id, device=device, compute_type=compute))
        except RuntimeError as e:
            if i + 1 == len(options):
                raise
            print(f"  whisper: {device} failed ({e}); falling back to CPU", file=sys.stderr)
    return None


def to_mlx_json(segments, language):
    segs = [{"id": s.id, "seek": s.seek, "start": s.start, "end": s.end, "text": s.text, "tokens": list(s.tokens),
             "temperature": s.temperature, "avg_logprob": s.avg_logprob, "compression_ratio": s.compression_ratio,
             "no_speech_prob": s.no_speech_prob} for s in segments]
    return {"text": "".join(s["text"] for s in segs), "segments": segs, "language": language}


def main():
    a = args()
    sr, x = read_wav(a.audio)
    if sr != 16000:
        sys.exit(f"whisper_fw: {a.audio}: {sr} Hz, expected 16000 (vidx writes 16 kHz mono)")

    def transcribe(model):
        segments, info = model.transcribe(x, language=a.language, initial_prompt=a.initial_prompt,
                                          condition_on_previous_text=a.condition_on_previous_text == "True",
                                          clip_timestamps=a.clip_timestamps, beam_size=1)
        res = []
        for s in segments:  # a generator: the work happens here, inside run_model's CUDA fallback
            res.append(s)
            print(f"\r  whisper {s.end:.0f}/{info.duration:.0f} s", end="", file=sys.stderr, flush=True)
        print(file=sys.stderr)
        return to_mlx_json(res, a.language or info.language)

    result = run_model(a.model, transcribe)
    stem = a.output_name or os.path.splitext(os.path.basename(a.audio))[0]
    os.makedirs(a.output_dir, exist_ok=True)
    with open(os.path.join(a.output_dir, stem + ".json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False)


if __name__ == "__main__":
    unix_stderr()
    main()
