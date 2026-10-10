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
speech was skipped: fewer than skip_cps chars/s without fillers, the alternative more than ALT_RATIO
times longer, longer than ALT_MIN_CHARS and at most loop_cps chars/s (guards against repetition loops).
skip_cps and loop_cps come from the region language's row in languages.py (they depend on the script).

Sources and their language: gigaam = ru, parakeet = any of its 25 languages (none is forced; on other
languages it outputs gibberish), "<engine>@<lang>" = that language (mixed runs), youtube-<kind>@<lang>
from the track, everything else = the only language.
Subtitles, titles and OCR text come from outside, so they are escaped before going into Markdown.
"""
import argparse
import glob
import json
import os
import re
import sys

import languages
from common import hms, load_segments

WINDOW_S, PARA_S = 40, 55
ALT_RATIO, ALT_MIN_CHARS, SPAN_INSIDE = 2, 80, 0.6
BASE_ORDER = ["gigaam", "whisper-large-v3", "parakeet", "whisper-turbo", "apple", "youtube-manual",
              "youtube-auto", "vosk"]
ALT = ["parakeet", "whisper-large-v3"]

W, E = languages.W, languages.E  # word boundaries that also work next to Cyrillic (glossary <W>, <E>)


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


# A "[" that opens an image (![), a link ([text](url)), a reference ([text][ref]) or a link definition
# ([ref]: url). Other punctuation (! * _ | and lone brackets) is left alone: it cannot inject anything.
LINK_OPEN = re.compile(r"(?<=!)\[|\[(?=[^\]]*\][(\[:])")


def md(text):
    """Escape outside text for Markdown: no HTML, images, links, code spans, headings, lists or quotes
    sneaking in, while ordinary punctuation stays readable (усім! not усім\\!)."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("`", "\\`")
    text = LINK_OPEN.sub(r"\\[", text)
    return re.sub(r"^(\s*)([#+*-])", r"\1\\\2", text)


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


def skipped_speech(got, alt, dur, row):
    """A base chunk of `got` chars (fillers removed) over dur s looks like the engine skipped speech
    that the alternative (`alt` chars, same span) has. row: languages.get(region language)."""
    return got / dur < row.skip_cps and alt > ALT_RATIO * got and alt > ALT_MIN_CHARS and alt <= row.loop_cps * dur


def span(segs, a, e, joiner=" "):
    """Text of the segments that lie mostly inside [a, e]."""
    return joiner.join(t for s0, e0, t in segs
                       if e0 > s0 and max(0, min(e0, e) - max(s0, a)) >= SPAN_INSIDE * (e0 - s0))


SENTENCE_START = re.compile(r"(^|[.!?…]\s+)([¿¡]?\w)")


def capitalise(t):
    """Upper-case the first letter of the text and of each sentence: works for every cased alphabet
    (і, ї, ў, ä, é...), changes nothing in scripts without case."""
    return SENTENCE_START.sub(lambda m: m[1] + m[2].upper(), t)


def join_chunks(chunks):
    """[(text, joiner)] → one string. Each chunk is preceded by its language's joiner ("" for Chinese
    and Japanese), or by a space where the joiner changes (a switch between such languages)."""
    res = ""
    for i, (t, j) in enumerate(chunks):
        if i:
            res += j if chunks[i - 1][1] == j else " "
        res += t
    return res


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

    def fits(name, lang):
        if lang_of(name) is None:  # parakeet: not told a language
            return languages.get(lang).parakeet
        return lang_of(name) == lang

    def unfill(t, row):  # hesitation sounds of the region's language removed
        return re.sub(row.fillers, "", t) if row.fillers else t

    def clean(t, row):
        t = unfill(t, row)
        for rx, repl in rules:
            t = rx.sub(repl, t)
        t = re.sub(r"\s{2,}", " ", t).strip().strip(",").strip()
        return capitalise(t)

    # --- merged transcript, region by region ---
    regions = [{"start": 0.0, "end": float("inf"), "lang": langs[0]}]
    if len(langs) > 1:
        regions = read_json(os.path.join(out, "regions.json")) or regions
        regions[-1]["end"] = float("inf")
    merged, fallbacks, used = [], 0, {}
    for r in regions:
        lang, r0, r1 = r["lang"], r["start"], r["end"]
        row = languages.get(lang)
        usable = [n for n in sources if fits(n, lang)]
        base = next((n for e in BASE_ORDER for n in usable if engine(n) == e), None)
        if not base:
            continue
        alts = [n for e in ALT for n in usable if engine(n) == e and n != base]
        for s0, e0, text in nodup([s for s in sources[base] if r0 <= (s[0] + s[1]) / 2 < r1]):
            if any(c0 <= s0 < c1 for c0, c1 in cuts):
                continue
            src, dur = base, max(1.0, e0 - s0)
            got = len(unfill(text, row))
            alt, alt_src = max([(span(sources[n], s0, e0, row.joiner), n) for n in alts] or [("", "")],
                               key=lambda x: len(x[0]))
            if skipped_speech(got, len(alt), dur, row):
                text, src, fallbacks = alt, alt_src, fallbacks + 1
            text = clean(text, row)
            if text:
                merged.append((s0, e0, text, row.joiner))
                used[f"{src} [{lang}]"] = used.get(f"{src} [{lang}]", 0) + 1
    merged.sort()

    paras, cur, start = [], [], None  # (start, text) or (start, None, chapter title)
    chapters, ch = info.get("chapters") or [], 0
    for s0, e0, t, joiner in merged:
        while ch < len(chapters) and chapters[ch]["start_time"] <= s0:
            if cur:
                paras.append((start, join_chunks(cur)))
                cur, start = [], None
            paras.append((chapters[ch]["start_time"], None, chapters[ch]["title"]))
            ch += 1
        start = s0 if start is None else start
        cur.append((t, joiner))
        if e0 - start >= PARA_S:
            paras.append((start, join_chunks(cur)))
            cur, start = [], None
    if cur:
        paras.append((start, join_chunks(cur)))

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
        f.write("".join(f"[{hms(s0)}] {t}\n" for s0, _, t, _ in merged))

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
                    f.write(f"- **{name}:** {md(languages.get(lang_of(name) or langs[0]).joiner.join(txt))}\n")
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
