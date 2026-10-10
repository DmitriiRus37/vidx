"""Split mixed-language speech into single-language regions (runs in mlx-whisper's own Python).

usage: regions.py <parakeet.json> <langs, e.g. ru,en> <audio.wav 16k mono> <whisper model> <out.json>

1. Each parakeet sentence is labelled by script: more Cyrillic than Latin letters → ru, otherwise the
   other language of the list (so this tells Cyrillic from Latin languages, not English from German).
   Sentences without letters inherit the previous label.
2. parakeet often drops a few seconds right where the speaker switches language. Every gap longer
   than GAP_S between sentences gets its own label from whisper's language detection on that audio
   (a language outside the list → label of the previous sentence). Shorter gaps are split in half.
3. Consecutive pieces with the same label form a region. First region starts at 0, last ends at the
   end of the audio.
out: [{"start", "end", "lang"}]
"""
import re
import sys

from common import dump_json, is_silent, load_segments, read_wav

GAP_S = 1.0

src, langs, audio, model, out = sys.argv[1], sys.argv[2].split(","), sys.argv[3], sys.argv[4], sys.argv[5]
other = next((lang for lang in langs if lang != "ru"), langs[0])
sents = load_segments(src)
sr, x = read_wav(audio)
dur = len(x) / sr


def script(text, prev):
    cyr, lat = len(re.findall(r"[А-Яа-яЁё]", text)), len(re.findall(r"[A-Za-z]", text))
    if not (cyr or lat):
        return prev
    return "ru" if cyr >= lat and "ru" in langs else other


def detect(a, b, prev):
    clip = x[int(a * sr):int(b * sr)]
    if len(clip) < sr or is_silent(clip):
        return None  # no label: the gap is split between its neighbours
    import mlx_whisper

    lang = mlx_whisper.transcribe(clip, path_or_hf_repo=model, verbose=None).get("language")
    return lang if lang in langs else prev


pieces, prev, t = [], langs[0], 0.0  # (start, end, lang or None)
for s0, e0, text in sents:
    if s0 - t > GAP_S:
        pieces.append((t, s0, detect(t, s0, prev)))
    elif s0 > t:
        pieces.append((t, s0, None))
    prev = script(text, prev)
    pieces.append((s0, e0, prev))
    t = e0
if dur > t:
    pieces.append((t, dur, None))

labelled = []
for i, (a, b, lang) in enumerate(pieces):
    if lang is not None:
        labelled.append((a, b, lang))
        continue
    left = next((p[2] for p in reversed(pieces[:i]) if p[2]), None)
    right = next((p[2] for p in pieces[i + 1:] if p[2]), None)
    m = (a + b) / 2
    labelled += [(a, m, left or right or langs[0]), (m, b, right or left or langs[0])]

regions = []
for a, b, lang in labelled:
    if b <= a:
        continue
    if regions and regions[-1]["lang"] == lang:
        regions[-1]["end"] = round(b, 2)
    else:
        regions.append({"start": round(a, 2), "end": round(b, 2), "lang": lang})
dump_json(regions, out)
print("  regions: " + ", ".join(f"{r['lang']} {r['start']:.1f}-{r['end']:.1f}s" for r in regions), file=sys.stderr)
