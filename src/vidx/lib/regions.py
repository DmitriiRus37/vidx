"""Split mixed-language speech into single-language regions (runs in the language-ID backend's Python).

usage: regions.py <language-ID model> <langs, e.g. uk,en,de> <audio.wav 16k mono> <out.json> [parakeet.json]
       (vidx runs it through lid_py, which picks the interpreter and the model)

1. Pieces: the audio is cut in the middle of every pause (at least PAUSE_S of frames QUIET_DB below
   the loud ones). A piece shorter than SEG_MIN_S joins the neighbour behind the shorter pause (the
   longer pause is the likelier language switch); a piece longer than SEG_MAX_S is cut again at its
   quietest points (common.pause_cuts, the cutter GigaAM uses). Silent pieces get no language.
2. Language ID per piece (lid.load(model), today whisper turbo): probabilities restricted to the listed
   languages and renormalised. Works for any number of languages and for same-script pairs (ru/uk, de/fr): it listens
   to the audio, not to the letters of a transcript.
3. Smoothing. A piece is trusted if it is at least SHORT_S long and its top language has at least
   MIN_CONF of the probability. Every other piece takes its neighbours' language: the same one if both
   agree, else the one its own probabilities prefer (silence: split in half). Equal neighbours join
   into regions; a region shorter than MIN_REGION_S joins the neighbour its pieces prefer.
4. Boundary refinement: a piece next to a boundary may hold the end of one language and the start of
   the next (the switch pause was not the longest). It is cut at its longest inner pause (each part at
   least REFINE_MIN_S); if the part touching the boundary is in the other region's language, the
   boundary moves to that pause.
5. With parakeet.json (given only when parakeet knows every listed language), each boundary moves to
   the nearest pause between parakeet sentences within SNAP_S: the middle of a pause up to SNAP_GAP_S,
   either edge of a longer gap (parakeet often drops the words right at a switch).
6. A warning on stderr when two listed languages could not be told apart: over the pieces where they
   are the top two, the top one averaged less than PAIR_WARN of their probability.
out: [{"start", "end", "lang"}], first region from 0, last to the end of the audio

Porting the language ID to another backend changes nothing here: only lid.py's load() and vidx's
LID_MODEL / lid_py (the interpreter and the model id); see lid.py.
"""
import sys
from dataclasses import dataclass

from common import (
    FRAME_S,
    SILENCE_RMS,
    dump_json,
    frame_rms,
    is_silent,
    load_segments,
    pause_cuts,
    read_wav,
    top,
    unix_stderr,
)

SEG_MIN_S, SEG_MAX_S = 2, 8
PAUSE_S, QUIET_DB, LOUD_PERCENTILE = 0.2, 26, 90
SHORT_S, MIN_CONF, MIN_REGION_S = 2, 0.6, 3
REFINE_MIN_S = 1
SNAP_S, SNAP_GAP_S = 1.5, 1.0
PAIR_WARN, PAIR_MIN_PIECES = 0.75, 3


@dataclass
class Piece:
    start: float
    end: float
    probs: dict | None  # restricted to the listed languages, sums to 1; None = silence


def restrict(probs, langs):
    """whisper's probabilities over all languages → renormalised over the listed ones (None if 0)."""
    p = {lang: float(probs.get(lang, 0.0)) for lang in langs}
    total = sum(p.values())
    return {lang: v / total for lang, v in p.items()} if total > 0 else None


class Pauses:
    """Quiet stretches of the audio, from FRAME_S loudness frames."""

    def __init__(self, x, sr):
        import numpy as np

        self.sr, self.fr = sr, int(FRAME_S * sr)
        self.e = frame_rms(x, sr)
        n = len(self.e)
        loud = np.percentile(self.e, LOUD_PERCENTILE) if n else 0.0
        quiet = self.e < max(SILENCE_RMS, loud * 10 ** (-QUIET_DB / 20))
        self.runs, i = [], 0  # [(first frame, end frame)] of quiet stretches
        while i < n:
            if quiet[i]:
                j = i
                while j < n and quiet[j]:
                    j += 1
                self.runs.append((i, j))
                i = j
            else:
                i += 1

    def longest_inside(self, a, b, margin):
        """Sample position of the middle of the longest pause in [a + margin, b - margin] (samples), or of
        the quietest frame there; None if the span is too short."""
        lo, hi = -(-(a + margin) // self.fr), (b - margin) // self.fr
        if hi <= lo:
            return None
        runs = [(max(i, lo), min(j, hi)) for i, j in self.runs if i < hi and j > lo]
        if runs:
            i, j = max(runs, key=lambda r: r[1] - r[0])
            return (i + j) // 2 * self.fr
        return (lo + int(self.e[lo:hi].argmin())) * self.fr


def pieces_at_pauses(x, sr, pauses=None):
    """Audio → [(start, end)] sample ranges covering all of it (step 1 of the module doc)."""
    pauses = pauses or Pauses(x, sr)
    fr, n = pauses.fr, len(pauses.e)
    cuts, plen = [0], []  # plen[k]: length of the pause at cuts[k + 1]
    for i, j in pauses.runs:
        if (j - i) * FRAME_S >= PAUSE_S and i > 0 and j < n:
            cuts.append((i + j) // 2 * fr)
            plen.append(j - i)
    cuts.append(len(x))
    while len(cuts) > 2:
        lens = [b - a for a, b in zip(cuts, cuts[1:], strict=False)]
        k = min(range(len(lens)), key=lens.__getitem__)
        if lens[k] >= SEG_MIN_S * sr:
            break
        # drop the cut behind the shorter pause: plen[k - 1] (left) or plen[k] (right)
        side = k - 1 if k == len(lens) - 1 or (k > 0 and plen[k - 1] <= plen[k]) else k
        del cuts[side + 1], plen[side]
    res = []
    for a, b in zip(cuts, cuts[1:], strict=False):
        if b - a > SEG_MAX_S * sr:
            res += [(a + c, a + d) for c, d in pause_cuts(x[a:b], sr, SEG_MIN_S, SEG_MAX_S)]
        else:
            res.append((a, b))
    return res


def probs_of(x, a, b, langs, detect):
    clip = x[a:b]
    return None if is_silent(clip) else restrict(detect(clip), langs)


def lid_pieces(x, sr, langs, detect, pauses=None):
    """Audio → [Piece] covering all of it, each with its restricted language probabilities."""
    return [Piece(a / sr, b / sr, probs_of(x, a, b, langs, detect)) for a, b in pieces_at_pauses(x, sr, pauses)]


def join(labelled):
    """[(start, end, lang)] → regions, consecutive equal languages joined."""
    regions = []
    for a, b, lang in labelled:
        if b <= a:
            continue
        if regions and regions[-1]["lang"] == lang:
            regions[-1]["end"] = b
        else:
            regions.append({"start": a, "end": b, "lang": lang})
    return regions


def preference(pieces, a, b, lang):
    """How much the pieces inside [a, b] lean to lang: probability x seconds."""
    return sum(p.probs[lang] * (p.end - p.start) for p in pieces if p.probs and a <= (p.start + p.end) / 2 < b)


def smooth(pieces, langs):
    """[Piece] → regions [{"start", "end", "lang"}] (step 3 of the module doc)."""
    sure = [p.probs is not None and p.end - p.start >= SHORT_S and max(p.probs.values()) >= MIN_CONF
            for p in pieces]
    labelled = []
    for i, p in enumerate(pieces):
        if sure[i]:
            labelled.append((p.start, p.end, top(p.probs)))
            continue
        left = next((top(pieces[k].probs) for k in range(i - 1, -1, -1) if sure[k]), None)
        right = next((top(pieces[k].probs) for k in range(i + 1, len(pieces)) if sure[k]), None)
        if left == right or left is None or right is None:
            lang = left or right or (top(p.probs) if p.probs else langs[0])
            labelled.append((p.start, p.end, lang))
        elif p.probs:
            labelled.append((p.start, p.end, left if p.probs[left] >= p.probs[right] else right))
        else:
            m = (p.start + p.end) / 2
            labelled += [(p.start, m, left), (m, p.end, right)]
    regions = join(labelled)
    while len(regions) > 1:
        short = [i for i, r in enumerate(regions) if r["end"] - r["start"] < MIN_REGION_S]
        if not short:
            break
        i = min(short, key=lambda k: regions[k]["end"] - regions[k]["start"])
        r = regions[i]
        near = [regions[k] for k in (i - 1, i + 1) if 0 <= k < len(regions)]
        best = max(near, key=lambda n: (preference(pieces, r["start"], r["end"], n["lang"]), n["end"] - n["start"]))
        r["lang"] = best["lang"]
        regions = join([(x["start"], x["end"], x["lang"]) for x in regions])
    return [{"start": round(r["start"], 2), "end": round(r["end"], 2), "lang": r["lang"]} for r in regions]


def refine(regions, pieces, x, sr, langs, detect, pauses):
    """Move boundaries to the switch inside a piece that straddles it (step 4 of the module doc)."""
    res = [dict(r) for r in regions]
    margin = int(REFINE_MIN_S * sr)
    for left, right in zip(res, res[1:], strict=False):
        t = left["end"]
        before = next((p for p in pieces if abs(p.end - t) < 0.01), None)
        after = next((p for p in pieces if abs(p.start - t) < 0.01), None)
        # (piece, its part next to the boundary, language that part should have to move the boundary)
        for p, near_end, other in ((before, True, right["lang"]), (after, False, left["lang"])):
            if p is None:
                continue
            a, b = int(p.start * sr), int(p.end * sr)
            m = pauses.longest_inside(a, b, margin)
            if m is None:
                continue
            probs = probs_of(x, *((m, b) if near_end else (a, m)), langs, detect)
            if probs and top(probs) == other and probs[other] >= MIN_CONF:
                left["end"] = right["start"] = round(m / sr, 2)
                break
    return [r for r in res if r["end"] > r["start"]]


def snap(regions, sentences):
    """Move each boundary to the nearest pause between parakeet sentences (within SNAP_S): the middle of
    a short pause, or either edge of a long gap (parakeet often drops the words at a switch)."""
    pauses = []
    for (_, e0, _), (s1, _, _) in zip(sentences, sentences[1:], strict=False):
        if s1 >= e0:
            pauses += [(e0 + s1) / 2] if s1 - e0 <= SNAP_GAP_S else [e0, s1]
    res = [dict(r) for r in regions]
    for left, right in zip(res, res[1:], strict=False):
        t = left["end"]
        near = min(pauses, key=lambda p: abs(p - t), default=None)
        if near is not None and abs(near - t) <= SNAP_S and left["start"] < near < right["end"]:
            left["end"] = right["start"] = round(near, 2)
    return res


def pair_warnings(pieces, langs):
    """Listed language pairs that language ID could not tell apart (step 6 of the module doc)."""
    shares = {}
    for p in pieces:
        if p.probs and len(p.probs) > 1:  # one language: nothing to tell apart
            a = top(p.probs)
            b = top({k: v for k, v in p.probs.items() if k != a})
            pair = tuple(sorted((a, b), key=langs.index))
            shares.setdefault(pair, []).append(p.probs[a] / (p.probs[a] + p.probs[b]))
    return [pair for pair, v in sorted(shares.items(), key=lambda kv: [langs.index(x) for x in kv[0]])
            if len(v) >= PAIR_MIN_PIECES and sum(v) / len(v) < PAIR_WARN]


def split(x, sr, langs, detect, sentences=None):
    """Audio → (regions, pieces); sentences = parakeet's [(start, end, text)] to snap the boundaries to."""
    pauses = Pauses(x, sr)
    pieces = lid_pieces(x, sr, langs, detect, pauses)
    regions = refine(smooth(pieces, langs), pieces, x, sr, langs, detect, pauses)
    return (snap(regions, sentences) if sentences else regions), pieces


def main():
    model, langs, audio, out = sys.argv[1], list(dict.fromkeys(sys.argv[2].split(","))), sys.argv[3], sys.argv[4]
    sentences = load_segments(sys.argv[5]) if len(sys.argv) > 5 else None
    import lid

    sr, x = read_wav(audio)
    regions, pieces = split(x, sr, langs, lid.load(model), sentences)
    for a, b in pair_warnings(pieces, langs):
        print(f"  warn: {a}/{b} could not be separated reliably (language ID unsure between them)", file=sys.stderr)
    dump_json(regions, out)
    print("  regions: " + ", ".join(f"{r['lang']} {r['start']:.1f}-{r['end']:.1f}s" for r in regions),
          file=sys.stderr)


if __name__ == "__main__":
    unix_stderr()
    main()
