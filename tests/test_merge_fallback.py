"""Characterisation of the fallback rule: one base segment per 10 s slot, each probing one threshold.

A base (GigaAM) chunk is swapped for the longer parakeet/whisper text over the same span when it
looks like speech was skipped: < skip_cps chars/s without fillers, the alternative > ALT_RATIO times
longer and > ALT_MIN_CHARS, and <= loop_cps chars/s (ru: 6 and 25). Each probe sits just next to one threshold, so
moving any threshold flips at least one expectation.
"""
import json

from helpers import run_merge

SLOT = 10.0


def text(n, word="слово"):
    """Exactly n characters of Russian-looking text (vary the word: identical neighbours are deduplicated)."""
    return ((word + " ") * (n // len(word) + 1))[:n].strip().ljust(n, "х")


FILLERS = "Э-э-э-э, ммммм, а-а-а-а, хммм, " * 2 + "ну"  # 63 chars, only "ну" survives FILL
# name: (base text, parakeet (start, end, text) relative to the slot or None, whisper ditto, replaced?)
PROBES = {
    "control": ("Да.", (0, 10, text(120)), None, True),
    "skip_cps_below": (text(59), (0, 10, text(130)), None, True),
    "skip_cps_above": (text(61, "текст"), (0, 10, text(130)), None, False),
    "ratio_and_min_above": (text(40), None, (0, 10, text(81)), True),
    "ratio_and_min_below": (text(40, "текст"), None, (0, 10, text(79)), False),
    "min_chars_below": (text(30), None, (0, 10, text(79)), False),
    "loop_cps_at": (text(10), (0, 10, text(250)), None, True),
    "loop_cps_above": (text(10, "текст"), (0, 10, text(251)), None, False),
    "span_inside_65pct": (text(5), (3.5, 13.5, text(150)), None, True),
    "span_inside_55pct": (text(5, "текст"), (4.5, 14.5, text(150)), None, False),
    "fillers_not_counted": (FILLERS, (0, 10, text(130)), None, True),
    "longer_alt_wins": (text(5), (0, 10, text(100)), (0, 10, text(140)), True),
}


def build(tmp_path):
    out = tmp_path / "fb_vidx-out"
    (out / "asr").mkdir(parents=True)
    giga, para, whis = [], [], []
    for k, (base, p, w, _) in enumerate(PROBES.values()):
        t0 = k * SLOT * 2  # empty slot in between: span_inside probes may reach past their own slot
        giga.append({"start": t0, "end": t0 + SLOT, "text": base})
        for alt, segs in ((p, para), (w, whis)):
            if alt:
                segs.append({"start": t0 + alt[0], "end": t0 + alt[1], "text": alt[2]})
    for name, segs in (("gigaam", giga), ("parakeet", para), ("whisper-large-v3", whis)):
        (out / "asr" / f"{name}.json").write_text(json.dumps({"segments": segs}, ensure_ascii=False))
    return out


def test_fallback_thresholds(tmp_path):
    out = run_merge(build(tmp_path), "--langs", "ru")
    lines = (out / "transcript.txt").read_text().splitlines()
    assert len(lines) == len(PROBES)
    for line, (name, (base, p, w, replaced)) in zip(lines, PROBES.items(), strict=True):
        alts = [a[2] for a in (p, w) if a]
        got_alt = any(a[:20].capitalize() in line for a in alts) and len(line) > len(base) + 20
        assert got_alt == replaced, f"{name}: {line[:60]}"
    head = (out / "transcript.md").read_text()
    assert f"fallbacks to parakeet/whisper: {sum(p[3] for p in PROBES.values())}" in head
