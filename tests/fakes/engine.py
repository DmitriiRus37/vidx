"""Fake engines for the vidx tests: same command lines and output files as the real ones, canned text.

usage: engine.py <engine> <the real engine's arguments...>   (called from tests/fakes/hook.sh)
Waits VIDX_FAKE_DELAY seconds (its pid is in VIDX_FAKE_LOG.pids/ meanwhile), fails (exit 1, no
output) if the engine is in VIDX_FAKE_FAIL, logs the call to VIDX_FAKE_LOG. Output:
VIDX_FAKE_DATA/<stem>.json if present, else one segment per clip (or for the whole audio) with the
text "Fake <engine> <lang>."
"""
import json
import os
import shutil
import sys
import time
import wave


def arg(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def duration(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


def canned(stem, dst):
    src = os.path.join(os.environ.get("VIDX_FAKE_DATA", ""), stem + ".json")
    if os.environ.get("VIDX_FAKE_DATA") and os.path.exists(src):
        shutil.copy(src, dst)
        return True
    return False


def write(dst, obj):
    with open(dst, "w") as f:
        json.dump(obj, f, ensure_ascii=False)


def segs(spans, text):
    return [{"start": a, "end": b, "text": text} for a, b in spans]


def whisper(engine, args):
    out = os.path.join(arg(args, "-o"), arg(args, "--output-name") + ".json")
    if canned(arg(args, "--output-name"), out):
        return
    lang, clips = arg(args, "--language"), arg(args, "--clip-timestamps")
    if clips:
        c = [float(x) for x in clips.split(",")]
        spans = list(zip(c[::2], c[1::2], strict=True))
    else:
        spans = [(0.0, duration(args[0]))]
    write(out, {"segments": segs(spans, f"Fake {engine} {lang}."), "language": lang})


def main():
    engine, args = sys.argv[1], sys.argv[2:]
    if engine == "whisper" and "turbo" in arg(args, "--model", ""):
        engine = "turbo"
    t0 = time.time()
    query = args == ["--locales"]  # asked while queueing, not a job: no delay
    if os.environ.get("VIDX_FAKE_LOG") and not query:  # lets a test see which fakes are running now
        os.makedirs(os.environ["VIDX_FAKE_LOG"] + ".pids", exist_ok=True)
        open(os.path.join(os.environ["VIDX_FAKE_LOG"] + ".pids", str(os.getpid())), "w").close()
    time.sleep(0 if query else float(os.environ.get("VIDX_FAKE_DELAY", "0")))
    failed = engine in os.environ.get("VIDX_FAKE_FAIL", "").split(",")
    if os.environ.get("VIDX_FAKE_LOG"):
        with open(os.environ["VIDX_FAKE_LOG"], "a") as f:
            f.write(json.dumps({"engine": engine, "args": args, "start": t0, "end": time.time()}) + "\n")
    if failed:
        sys.exit(f"fake {engine}: failing as asked (VIDX_FAKE_FAIL)")
    if engine in ("whisper", "turbo"):
        whisper(engine, args)
    elif engine == "parakeet":
        out = os.path.join(arg(args, "--output-dir"), "parakeet.json")
        if not canned("parakeet", out):
            write(out, {"sentences": segs([(0.0, duration(args[0]))], "Fake parakeet.")})
    elif engine == "gigaam":  # audio out [regions]
        if not canned("gigaam", args[1]):
            spans = [(0.0, duration(args[0]))]
            if len(args) > 2:
                with open(args[2]) as f:
                    spans = [(r["start"], r["end"]) for r in json.load(f) if r["lang"] == "ru"]
            write(args[1], {"engine": "gigaam", "segments": segs(spans, "Fake gigaam ru.")})
    elif engine == "vosk":  # model audio out
        lang = os.path.basename(args[0]).split("-")[2]
        if not canned(os.path.basename(args[2])[:-5], args[2]):
            write(args[2], {"engine": "vosk", "segments": segs([(0.0, duration(args[1]))], f"Fake vosk {lang}.")})
    elif engine == "apple":  # --locales | lang audio out
        if args == ["--locales"]:
            print("supported: " + os.environ.get("VIDX_FAKE_APPLE_LOCALES", "en-US de-DE fr-FR"))
            print("installed: en-US")
        elif not canned(os.path.basename(args[2])[:-5], args[2]):
            write(args[2], {"engine": "apple", "segments": segs([(0.0, duration(args[1]))], f"Fake apple {args[0]}.")})
    elif engine == "ocr":  # langs image...
        for p in args[1:]:
            print(json.dumps({"file": os.path.basename(p), "text": f"Fake OCR {args[0]}"}))
    elif engine == "detect_lang":
        print(os.environ.get("VIDX_FAKE_LANG", "ru"))
    elif engine == "regions":  # parakeet.json langs audio model out
        langs, out = args[1].split(","), args[4]
        if not canned("regions", out):
            d = duration(args[2])
            write(out, [{"start": round(i * d / len(langs), 2), "end": round((i + 1) * d / len(langs), 2), "lang": la}
                        for i, la in enumerate(langs)])
    else:
        sys.exit(f"fake engine: unknown engine {engine}")


if __name__ == "__main__":
    main()
