"""Fallback where parakeet does not know the language (ja, zh, hi, ar, be, ko...): the base chunk is swapped
for whisper turbo or Apple, and a realistic 5-10 s chunk of Chinese or Japanese speech qualifies.

Texts and timings from the real whisper/turbo output of the TTS clips in tests/fixtures/merge/{zh,ja}; the
base chunk is cut short the way an engine that skipped speech leaves it."""
import json

from helpers import run_merge

ZH_REST = "大约20度,星期天下午会下雨,所以请带上雨伞和暖和的衣服。"
ZH_FULL = "今天我们聊一聊周末的天气,星期六天气晴朗温暖,"  # 24 chars in 6 s: 4 chars/s, normal for Chinese
ZH_PARAKEET = "Sintian woman liao yi liao jongwoda tiensi, sin si liao tiensi languan, tai yue ar shedu."
JA_FULL = "今日は週末の天気について話します。土曜日は晴れて暖かく、"  # 28 chars in 5.5 s
JA_REST = "日曜日の午後は雨が降るので、傘と暖かい服を持って行ってください。"


def write(out, name, segs):
    (out / "asr").mkdir(parents=True, exist_ok=True)
    (out / "asr" / f"{name}.json").write_text(json.dumps({"segments": [
        {"start": a, "end": b, "text": t} for a, b, t in segs]}, ensure_ascii=False), encoding="utf-8", newline="\n")


def header(out, line):
    return next(x for x in (out / "transcript.md").read_text(encoding="utf-8").splitlines() if x.startswith(line))


def test_zh_falls_back_to_turbo_not_parakeet(tmp_path):
    out = tmp_path / "zh_vidx-out"
    write(out, "whisper-large-v3", [(0, 6, "今天我们聊一聊"), (6, 11.78, ZH_REST)])  # 7 chars in 6 s: skipped
    write(out, "whisper-turbo", [(0, 6, ZH_FULL), (6, 11.78, ZH_REST)])
    write(out, "parakeet", [(0.08, 12.16, ZH_PARAKEET)])
    run_merge(out, "--langs", "zh")
    want = ZH_FULL.rstrip(",")  # the clean-up drops a trailing comma
    assert (out / "transcript.txt").read_text(encoding="utf-8") == f"[00:00:00] {want}\n[00:00:06] {ZH_REST}\n"
    assert header(out, "Chunks per source") == "Chunks per source: whisper-turbo [zh] 1, whisper-large-v3 [zh] 1  "
    assert "fallbacks to parakeet/whisper: 1" in header(out, "Languages")


def test_zh_falls_back_to_apple(tmp_path):
    out = tmp_path / "zh_vidx-out"
    write(out, "whisper-large-v3", [(0, 6, "今天我们聊一聊"), (6, 11.78, ZH_REST)])
    write(out, "apple", [(0, 6, ZH_FULL)])
    run_merge(out, "--langs", "zh")
    assert (out / "transcript.txt").read_text(encoding="utf-8").splitlines()[0] == f"[00:00:00] {ZH_FULL.rstrip(',')}"


def test_ja_region_of_a_mixed_run(tmp_path):
    out = tmp_path / "mix_vidx-out"
    write(out, "whisper-large-v3@ja", [(0, 5.5, "今日は"), (5.5, 13.34, JA_REST)])  # 3 chars in 5.5 s
    write(out, "whisper-turbo@ja", [(0, 5.5, JA_FULL), (5.5, 13.34, JA_REST)])
    write(out, "whisper-large-v3@en", [(14, 17, "And now in English.")])
    (out / "regions.json").write_text(json.dumps([{"start": 0, "end": 13.6, "lang": "ja"},
                                                  {"start": 13.6, "end": 17, "lang": "en"}]),
                                      encoding="utf-8", newline="\n")
    run_merge(out, "--langs", "ja,en")
    assert JA_FULL + JA_REST + " And now in English." in (out / "transcript.md").read_text(encoding="utf-8")


def test_normal_chinese_chunk_is_kept(tmp_path):
    """The real zh fixture: whisper's 4.4 chars/s is normal Chinese, nothing is replaced."""
    out = tmp_path / "zh_vidx-out"
    full = ZH_FULL + ZH_REST
    write(out, "whisper-large-v3", [(0, 11.78, full)])
    write(out, "whisper-turbo", [(0, 11.78, full + full)])  # longer, but the base did not skip anything
    run_merge(out, "--langs", "zh")
    assert "fallbacks to parakeet/whisper: 0" in header(out, "Languages")


def test_cjk_alternative_joined_without_spaces(tmp_path):
    """A fallback over two turbo segments joins them with the region's joiner: "" for Chinese."""
    out = tmp_path / "zh_vidx-out"
    write(out, "whisper-large-v3", [(0, 8, "今天")])
    write(out, "whisper-turbo", [(0, 4, "今天我们聊一聊周末的天气,"), (4, 8, "星期六天气晴朗温暖,大约20度。")])
    run_merge(out, "--langs", "zh")
    txt = (out / "transcript.txt").read_text(encoding="utf-8")
    assert txt == "[00:00:00] 今天我们聊一聊周末的天气,星期六天气晴朗温暖,大约20度。\n"


def test_parakeet_never_used_for_a_language_it_does_not_know(tmp_path):
    """Neither as the base (it comes before turbo in BASE_ORDER) nor as a fallback for a skipped chunk."""
    out = tmp_path / "zh_vidx-out"
    write(out, "parakeet", [(0.08, 12.16, ZH_PARAKEET)])
    write(out, "whisper-turbo", [(0, 6, "今天"), (6, 11.78, ZH_REST)])  # first chunk skipped speech
    run_merge(out, "--langs", "zh")
    assert (out / "transcript.txt").read_text(encoding="utf-8") == f"[00:00:00] 今天\n[00:00:06] {ZH_REST}\n"
    assert header(out, "Chunks per source") == "Chunks per source: whisper-turbo [zh] 2  "
