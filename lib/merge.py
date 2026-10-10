"""Align every text source in 40 s windows and build one merged transcript.

usage: merge.py <out_dir> [--langs ru,en] [--sources a,b] [--glossary FILE] [--cut START-END ...]
  --sources  asr/ file stems made by this run (default: every asr/*.json; stale files from runs with
             other options are ignored when given)
  --cut      seconds; chunks starting inside are dropped from the transcript
Reads asr/*.json, subs/*.json3, ocr/ocr.jsonl, frames/frames.json, info.json, regions.json.
Writes aligned.md, transcript.md, transcript.txt, ocr.md.

Merge, per language region (regions.json in mixed runs, else the whole file): base = the first
source in BASE_ORDER whose language fits the region. Each base segment is replaced by the longer of
parakeet/whisper over the same span (their segments at least SPAN_INSIDE inside it) when it looks like
speech was skipped: fewer than SKIP_CPS chars/s without fillers, the alternative more than ALT_RATIO
times longer, longer than ALT_MIN_CHARS and at most LOOP_CPS chars/s (guards against repetition loops).

Sources and their language: gigaam = ru, parakeet = any (no language forced), "<engine>@<lang>" =
that language (mixed runs), youtube-<kind>@<lang> from the track, everything else = the only language.
Subtitles, titles and OCR text come from outside, so they are escaped before going into Markdown.
"""
import argparse
import glob
import json
import os
import re
import sys

from common import hms, load_segments

WINDOW_S, PARA_S = 40, 55
SKIP_CPS, ALT_RATIO, ALT_MIN_CHARS, LOOP_CPS, SPAN_INSIDE = 6, 2, 80, 25, 0.6
BASE_ORDER = ["gigaam", "whisper-large-v3", "parakeet", "whisper-turbo", "apple", "youtube-manual",
              "youtube-auto", "vosk"]
ALT = ["parakeet", "whisper-large-v3"]

W = r"(?<![\wА-Яа-яЁё])"  # word boundaries that also work next to Cyrillic
E = r"(?![\wА-Яа-яЁё])"
# hesitation sounds only: а-а, э-э, ааа, ммм, хмм, и-и, ну-у, uh, um
FILL = re.compile(W + r"(?:[ИиНн]у?-[иу]+|[АаЭэ]+-[аэ]+(?:-[аэ]+)*|[Аа]{3,}|[Ээ]{3,}|[Хх]м+|[Мм]{3,}|м-м"
                  r"|[Uu]h+|[Uu]m+|[Uu]hm|[Ee]rm)" + E + r"[,.]?\s*")


def args():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--langs", default="ru")
    ap.add_argument("--sources")
    ap.add_argument("--glossary")
    ap.add_argument("--cut", action="append", default=[])
    return ap.parse_args()


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def load_json3(path):
    segs = []
    for e in read_json(path, {}).get("events", []):
        text = "".join(s.get("utf8", "") for s in e.get("segs", [])).replace("\n", " ").strip()
        if text:
            st = e["tStartMs"] / 1000
            segs.append((st, st + e.get("dDurationMs", 0) / 1000, text))
    return segs


def load_sources(out, only):
    sources = {}
    for p in sorted(glob.glob(os.path.join(out, "asr", "*.json"))):
        stem = os.path.basename(p)[:-5]
        if only is None or stem in only:
            sources[stem] = load_segments(p)
    for p in sorted(glob.glob(os.path.join(out, "subs", "*.json3"))):
        kind, lang = os.path.basename(p).split(".")[:2]  # manual.ru.json3 / auto.en-orig.json3
        sources[f"youtube-{kind}@{lang.split('-')[0]}"] = load_json3(p)
    return {k: v for k, v in sources.items() if v}


def load_ocr(out):
    frames = read_json(os.path.join(out, "frames", "frames.json"), [])
    path = os.path.join(out, "ocr", "ocr.jsonl")
    if not frames or not os.path.exists(path):
        return []
    t_of = {f["file"]: f["t"] for f in frames}
    rows = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r["text"].strip() and r["file"] in t_of:
                rows.append({"t": t_of[r["file"]], "file": r["file"], "text": r["text"].strip()})
    return sorted(rows, key=lambda r: r["t"])


def load_glossary(path):
    rules = []
    if path:
        with open(path) as f:
            for line in f:
                if "=>" in line and not line.lstrip().startswith("#"):
                    a, b = line.rstrip("\n").split("=>", 1)
                    rules.append((re.compile(a.strip().replace("<W>", W).replace("<E>", E)), b.strip()))
    return rules


def md(text):
    """Escape outside text for Markdown: no HTML, images, links or headings sneaking in."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"([\[\]!`*_|])", r"\\\1", text)
    return re.sub(r"^(\s*)([#>+-])", r"\1\\\2", text)


def fence(text):
    ticks = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, ticks + 1)


def nodup(segs):
    """Drop consecutive repeats (whisper sometimes loops on one segment)."""
    res, prev = [], None
    for s in segs:
        if s[2] != prev:
            res.append(s)
        prev = s[2]
    return res


def span(segs, a, e):
    """Text of the segments that lie mostly inside [a, e]."""
    return " ".join(t for s0, e0, t in segs if e0 > s0 and max(0, min(e0, e) - max(s0, a)) >= SPAN_INSIDE * (e0 - s0))


def main():
    a = args()
    out = a.out
    langs = a.langs.split(",")
    only = set(a.sources.split(",")) if a.sources else None
    cuts = [tuple(map(float, c.split("-"))) for c in a.cut]
    rules = load_glossary(a.glossary)
    sources = load_sources(out, only)
    ocr = load_ocr(out)
    info = read_json(os.path.join(out, "info.json"), {})

    def engine(name):
        return name.split("@")[0]

    def lang_of(name):
        if "@" in name:
            return name.split("@")[1]
        return {"gigaam": "ru", "parakeet": None}.get(name, langs[0])

    def clean(t):
        t = FILL.sub("", t)
        for rx, repl in rules:
            t = rx.sub(repl, t)
        t = re.sub(r"\s{2,}", " ", t).strip().strip(",").strip()
        t = re.sub(r"([.!?…]\s+)([а-яёa-z])", lambda m: m[1] + m[2].upper(), t)
        return t[:1].upper() + t[1:]

    # --- merged transcript, region by region ---
    regions = [{"start": 0.0, "end": float("inf"), "lang": langs[0]}]
    if len(langs) > 1:
        regions = read_json(os.path.join(out, "regions.json")) or regions
        regions[-1]["end"] = float("inf")
    merged, fallbacks, used = [], 0, {}
    for r in regions:
        lang, r0, r1 = r["lang"], r["start"], r["end"]
        fits = [n for n in sources if lang_of(n) in (lang, None)]
        base = next((n for e in BASE_ORDER for n in fits if engine(n) == e), None)
        if not base:
            continue
        alts = [n for e in ALT for n in fits if engine(n) == e and n != base]
        for s0, e0, text in nodup([s for s in sources[base] if r0 <= (s[0] + s[1]) / 2 < r1]):
            if any(c0 <= s0 < c1 for c0, c1 in cuts):
                continue
            src, dur = base, max(1.0, e0 - s0)
            got = len(FILL.sub("", text))
            alt, alt_src = max([(span(sources[n], s0, e0), n) for n in alts] or [("", "")], key=lambda x: len(x[0]))
            if (got / dur < SKIP_CPS and len(alt) > ALT_RATIO * got and len(alt) > ALT_MIN_CHARS
                    and len(alt) <= LOOP_CPS * dur):
                text, src, fallbacks = alt, alt_src, fallbacks + 1
            text = clean(text)
            if text:
                merged.append((s0, e0, text))
                used[f"{src} [{lang}]"] = used.get(f"{src} [{lang}]", 0) + 1
    merged.sort()

    paras, cur, start = [], [], None  # (start, text) or (start, None, chapter title)
    chapters, ch = info.get("chapters") or [], 0
    for s0, e0, t in merged:
        while ch < len(chapters) and chapters[ch]["start_time"] <= s0:
            if cur:
                paras.append((start, " ".join(cur)))
                cur, start = [], None
            paras.append((chapters[ch]["start_time"], None, chapters[ch]["title"]))
            ch += 1
        start = s0 if start is None else start
        cur.append(t)
        if e0 - start >= PARA_S:
            paras.append((start, " ".join(cur)))
            cur, start = [], None
    if cur:
        paras.append((start, " ".join(cur)))

    with open(os.path.join(out, "transcript.md"), "w") as f:
        f.write(f"# {md(info.get('title') or os.path.basename(out.rstrip('/')))}\n\n")
        if info.get("webpage_url"):
            f.write(f"Source: {md(info['webpage_url'])}  \n")
        f.write(f"Languages: {','.join(langs)}; language regions: {len(regions)}; "
                f"fallbacks to parakeet/whisper: {fallbacks}  \n")
        per_source = ", ".join(f"{k} {v}" for k, v in sorted(used.items(), key=lambda x: -x[1]))
        f.write(f"Chunks per source: {per_source}  \n")
        f.write(f"Text sources: {', '.join(sources) or 'none'}")
        f.write(f"; OCR on {len(ocr)} frames (ocr.md)\n\n" if ocr else "\n\n")
        f.writelines(f"## {hms(p[0])} · {md(p[2])}\n\n" if p[1] is None else f"**[{hms(p[0])}]** {md(p[1])}\n\n"
                     for p in paras)
    with open(os.path.join(out, "transcript.txt"), "w") as f:
        f.write("".join(f"[{hms(s0)}] {t}\n" for s0, _, t in merged))

    # --- aligned view: every source side by side in 40 s windows ---
    end = max([s[1] for v in sources.values() for s in v] + [r["t"] for r in ocr] + [0])
    with open(os.path.join(out, "aligned.md"), "w") as f:
        f.write(f"# Aligned sources ({WINDOW_S} s windows): {' '.join(sources)}\n")
        t = 0
        while t < end:
            f.write(f"\n## [{hms(t)}]\n")
            for name, segs in sources.items():
                txt = [x for _, _, x in nodup([s for s in segs if t <= (s[0] + s[1]) / 2 < t + WINDOW_S])]
                if txt:
                    f.write(f"- **{name}:** {md(' '.join(txt))}\n")
            for r in ocr:
                if t <= r["t"] < t + WINDOW_S:
                    one = " · ".join(r["text"].splitlines())
                    f.write(f"- **ocr {r['file']}:** {md(one[:400])}{'…' if len(one) > 400 else ''}\n")
            t += WINDOW_S

    if ocr:
        with open(os.path.join(out, "ocr.md"), "w") as f:
            f.write("# On-screen text (Apple Vision OCR)\n\n")
            for r in ocr:
                q = fence(r["text"])
                f.write(f"## {hms(r['t'])} · frames/unique/{r['file']}\n\n{q}\n{r['text']}\n{q}\n\n")

    print(f"  merge: {len(regions)} regions, {len(merged)} chunks {used}, {fallbacks} fallbacks, "
          f"ocr frames={len(ocr)}", file=sys.stderr)


if __name__ == "__main__":
    main()
