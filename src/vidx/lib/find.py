"""Find the timecode of a phrase in every text source.

usage: find.py <out_dir> <regex> [source]      source = asr file stem, e.g. whisper-large-v3, or "ocr"
"""
import glob
import json
import os
import re
import sys

from common import hms, load_segments


def main():
    out, pat = sys.argv[1], sys.argv[2]
    only = sys.argv[3] if len(sys.argv) > 3 else None
    rx = re.compile(pat, re.IGNORECASE)
    for p in sorted(glob.glob(os.path.join(out, "asr", "*.json"))):
        name = os.path.basename(p)[:-5]
        if only in (None, name):
            for s0, _, text in load_segments(p):
                if rx.search(text):
                    print(f"{hms(s0)}  {name:<17} {text[:160]}")
    frames, ocr = os.path.join(out, "frames", "frames.json"), os.path.join(out, "ocr", "ocr.jsonl")
    if only in (None, "ocr") and os.path.exists(frames) and os.path.exists(ocr):
        with open(frames, encoding="utf-8") as f:
            t_of = {x["file"]: x["t"] for x in json.load(f)}
        with open(ocr, encoding="utf-8") as f:
            rows = sorted((json.loads(line) for line in f), key=lambda r: t_of.get(r["file"], 0))
        for r in rows:
            for line in r["text"].splitlines():
                if rx.search(line):
                    t = hms(t_of.get(r["file"], 0))
                    print(f"{t}  {'ocr':<17} {line.strip()[:120]}   (frames/unique/{r['file']})")


if __name__ == "__main__":
    main()
