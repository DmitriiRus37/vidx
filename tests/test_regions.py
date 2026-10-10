"""regions.py v2: audio → pieces at pauses → language ID per piece → smoothed single-language regions."""
import numpy as np
import pytest
import regions
from common import pause_cuts
from regions import Piece, pair_warnings, pieces_at_pauses, restrict, smooth, snap, split

SR = 16000


def piece(a, b, lang=None, conf=0.95, langs=("uk", "en", "de")):
    if lang is None:
        return Piece(a, b, None)
    rest = (1 - conf) / (len(langs) - 1)
    return Piece(a, b, {la: conf if la == lang else rest for la in langs})


def seq(labels, step=2.5, langs=("uk", "en", "de")):
    return [piece(i * step, (i + 1) * step, la, langs=langs) for i, la in enumerate(labels)]


def as_tuples(rs):
    return [(r["lang"], r["start"], r["end"]) for r in rs]


def test_restrict_to_listed_languages():
    assert restrict({"ru": 0.6, "uk": 0.2, "en": 0.2}, ["ru", "uk"]) == pytest.approx({"ru": 0.75, "uk": 0.25})
    assert restrict({"pt": 1.0}, ["ru", "uk"]) is None  # nothing of the listed languages


def test_single_odd_piece_is_absorbed():
    assert as_tuples(smooth(seq(["uk", "uk", "en", "uk", "uk"]), ["uk", "en", "de"])) == [("uk", 0, 12.5)]


def test_three_languages_three_regions():
    got = smooth(seq(["uk", "uk", "en", "en", "en", "de", "de"]), ["uk", "en", "de"])
    assert as_tuples(got) == [("uk", 0, 5.0), ("en", 5.0, 12.5), ("de", 12.5, 17.5)]


def test_unsure_piece_goes_to_the_neighbour_it_prefers():
    unsure = Piece(5, 7.5, {"uk": 0.2, "en": 0.45, "de": 0.35})  # below MIN_CONF
    ps = seq(["uk", "uk"]) + [unsure] + [piece(7.5, 10, "de"), piece(10, 12.5, "de")]
    assert as_tuples(smooth(ps, ["uk", "en", "de"])) == [("uk", 0, 5.0), ("de", 5.0, 12.5)]
    unsure = Piece(5, 7.5, {"uk": 0.5, "en": 0.1, "de": 0.4})
    ps = seq(["uk", "uk"]) + [unsure] + [piece(7.5, 10, "de"), piece(10, 12.5, "de")]
    assert as_tuples(smooth(ps, ["uk", "en", "de"])) == [("uk", 0, 7.5), ("de", 7.5, 12.5)]


def test_silence_between_languages_is_split_in_half():
    ps = seq(["uk", "uk"]) + [Piece(5, 7, None)] + [piece(7, 10, "en"), piece(10, 13, "en")]
    assert as_tuples(smooth(ps, ["uk", "en", "de"])) == [("uk", 0, 6.0), ("en", 6.0, 13)]


def test_short_piece_is_not_trusted():
    ps = seq(["uk", "uk"]) + [piece(5, 6.5, "en")] + [piece(6.5, 9, "uk"), piece(9, 11.5, "uk")]
    assert as_tuples(smooth(ps, ["uk", "en", "de"])) == [("uk", 0, 11.5)]


def test_region_shorter_than_minimum_joins_a_neighbour():
    # en 2.8 s between uk and de: below MIN_REGION_S; its own probabilities lean to de
    odd = Piece(5, 7.8, {"uk": 0.05, "en": 0.6, "de": 0.35})
    ps = seq(["uk", "uk"]) + [odd] + [piece(7.8, 10.3, "de"), piece(10.3, 12.8, "de")]
    assert as_tuples(smooth(ps, ["uk", "en", "de"])) == [("uk", 0, 5.0), ("de", 5.0, 12.8)]


def test_first_and_last_short_regions_join_their_only_neighbour():
    ps = [piece(0, 2.5, "en")] + seq(["uk"] * 4, langs=("uk", "en", "de"))[1:] + [piece(10, 12.5, "de")]
    assert as_tuples(smooth(ps, ["uk", "en", "de"])) == [("uk", 0, 12.5)]


def test_one_language_only():
    assert as_tuples(smooth(seq(["uk"] * 3), ["uk", "en"])) == [("uk", 0, 7.5)]
    assert as_tuples(smooth([Piece(0, 9, None)], ["uk", "en"])) == [("uk", 0, 9)]


def test_snap_to_parakeet_sentence_pauses():
    rs = [{"start": 0, "end": 6.0, "lang": "uk"}, {"start": 6.0, "end": 12.0, "lang": "en"}]
    sents = [(0.2, 5.9, "a"), (6.9, 9.0, "b"), (9.3, 12.0, "c")]  # pause 5.9-6.9 → 6.4
    assert as_tuples(snap(rs, sents)) == [("uk", 0, 6.4), ("en", 6.4, 12.0)]
    none_near = [(0.2, 8.0, "a"), (8.4, 12.0, "b")]  # pause at 8.2: beyond SNAP_S
    assert as_tuples(snap(rs, none_near)) == [("uk", 0, 6.0), ("en", 6.0, 12.0)]
    dropped = [(0.2, 5.5, "a"), (8.0, 12.0, "b")]  # parakeet lost 5.5-8.0: its edges count, not 6.75
    assert as_tuples(snap(rs, dropped)) == [("uk", 0, 5.5), ("en", 5.5, 12.0)]


def test_pair_warning_when_two_languages_cannot_be_told_apart():
    shaky = [Piece(i * 3, i * 3 + 3, {"ru": 0.55, "uk": 0.45} if i % 2 else {"ru": 0.4, "uk": 0.6})
             for i in range(8)]
    assert pair_warnings(shaky, ["ru", "uk"]) == [("ru", "uk")]
    clear = [piece(i * 3, i * 3 + 3, "ru" if i < 4 else "uk", 0.97, ("ru", "uk")) for i in range(8)]
    assert pair_warnings(clear, ["ru", "uk"]) == []


def tone(freq, s):
    t = np.arange(int(s * SR)) / SR
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(s):
    return np.zeros(int(s * SR), dtype=np.float32)


FREQ = {"uk": 200.0, "en": 400.0, "de": 600.0}


def fake_lid(clip):
    """A language ID that hears the tone: uk 200 Hz, en 400 Hz, de 600 Hz."""
    spec = np.abs(np.fft.rfft(clip))
    f = np.argmax(spec) * SR / len(clip)
    lang = min(FREQ, key=lambda la: abs(FREQ[la] - f))
    return {la: 0.9 if la == lang else 0.05 for la in FREQ}


def test_pause_cuts_at_the_pauses_and_cover_everything():
    x = np.concatenate([tone(200, 3), silence(0.4), tone(200, 3), silence(0.4), tone(200, 3)])
    cuts = pause_cuts(x, SR, 2, 8)
    assert cuts[0][0] == 0 and cuts[-1][1] == len(x)
    assert all(b == c for (_, b), (c, _) in zip(cuts, cuts[1:], strict=False))
    inner = [b / SR for _, b in cuts[:-1]]
    assert all(3.0 <= t <= 3.4 or 6.4 <= t <= 6.8 for t in inner), inner


def test_pieces_cut_at_every_pause_then_short_ones_join_across_the_weaker_pause():
    # 3 s, pause .3, 1 s, pause .8, 3 s, pause .3, 12 s without a pause (cut again by the shared cutter)
    x = np.concatenate([tone(200, 3), silence(0.3), tone(200, 1), silence(0.8), tone(200, 3), silence(0.3),
                        tone(200, 12)])
    got = [(round(a / SR, 2), round(b / SR, 2)) for a, b in pieces_at_pauses(x, SR)]
    # the 1 s piece joined its left neighbour (0.3 s pause < 0.8 s pause); cuts in the middle of pauses
    assert got[:2] == [pytest.approx((0, 4.7), abs=0.03), pytest.approx((4.7, 8.25), abs=0.03)]
    assert all(regions.SEG_MIN_S - 0.03 <= b - a <= regions.SEG_MAX_S for a, b in got[:-1])  # one frame
    assert got[-1][1] == round(len(x) / SR, 1)


def test_split_three_languages_end_to_end():
    """Like mix3: uk 6.4 s, en 5.2 s, de 4.6 s; a short pause inside each and a longer one at a switch."""
    parts = [("uk", 6.4), ("en", 5.2), ("de", 4.6)]
    x = np.concatenate([np.concatenate([tone(FREQ[la], d / 2 - 0.5), silence(0.3), tone(FREQ[la], d / 2 - 0.3),
                                        silence(0.5)]) for la, d in parts])
    got = as_tuples(split(x, SR, ["uk", "en", "de"], fake_lid)[0])
    assert [g[0] for g in got] == ["uk", "en", "de"]
    assert abs(got[0][2] - 6.4) <= 1 and abs(got[1][2] - 11.6) <= 1
    assert got[-1][2] == pytest.approx(len(x) / SR, abs=0.01)


def test_boundary_moves_to_the_switch_inside_a_straddling_piece():
    """The switch pause (0.25 s) is shorter than the next one (0.6 s), so the short first en sentence
    joins the uk piece; refinement finds the switch again."""
    x = np.concatenate([tone(200, 3), silence(0.5), tone(200, 3), silence(0.25), tone(400, 1.5), silence(0.6),
                        tone(400, 4)])
    got = as_tuples(split(x, SR, ["uk", "en"], fake_lid)[0])
    assert [g[0] for g in got] == ["uk", "en"]
    assert got[0][2] == pytest.approx(6.625, abs=0.05)


def test_gigaam_cutter_unchanged():
    """GigaAM shares the cutter; pause_cuts(x, sr, 14, 22) must equal its previous built-in version."""
    import itertools

    def old_cuts(x, sr=16000):
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

    rng = np.random.default_rng(1)
    x = (rng.standard_normal(SR * 70) * np.repeat(rng.random(70 * 10), SR // 10)).astype(np.float32)
    old = old_cuts(x)
    old[-1] = (old[-1][0], len(x))  # gigaam_run extended the last chunk to the end itself
    assert pause_cuts(x, SR, 14, 22) == old


def test_constants_follow_the_plan():
    assert (regions.SEG_MIN_S, regions.SEG_MAX_S, regions.SHORT_S, regions.MIN_REGION_S) == (2, 8, 2, 3)


def test_main_warns_when_a_pair_cannot_be_separated(tmp_path, monkeypatch, capsys):
    import json
    import sys
    import types
    import wave

    x = np.concatenate([np.concatenate([tone(200, 2.5), silence(0.4)]) for _ in range(6)])
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())
    flip = iter([0.55, 0.45] * 10)
    shaky = types.SimpleNamespace(load=lambda model: lambda clip: {"ru": (p := next(flip)), "uk": 1 - p})
    monkeypatch.setitem(sys.modules, "lid", shaky)
    monkeypatch.setattr(sys, "argv", ["regions.py", "ru,uk", str(path), "turbo", str(tmp_path / "r.json")])
    regions.main()
    assert "ru/uk could not be separated reliably" in capsys.readouterr().err
    assert json.loads((tmp_path / "r.json").read_text())[0]["start"] == 0
