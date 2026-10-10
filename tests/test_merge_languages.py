"""Merge per language: real whisper/turbo/parakeet output of TTS clips (12-19 s, one language each)."""
import re

from helpers import check_golden, out_dir_from, run_merge


def merged(lang, tmp_path, *args):
    out = run_merge(out_dir_from(f"merge/{lang}", tmp_path, lang), "--langs", lang, *args)
    return out, (out / "transcript.txt").read_text()


def test_zh_uses_whisper_not_parakeet_gibberish(tmp_path):
    """whisper's correct Chinese runs at 4.4 chars/s; parakeet (no Chinese) romanises it."""
    out, txt = merged("zh", tmp_path)
    assert "今天我们聊一聊周末的天气" in txt
    assert not re.search(r"[A-Za-z]", txt), txt
    assert "parakeet" not in (out / "transcript.md").read_text().split("Chunks per source:")[1].split("\n")[0]
    check_golden("zh", out)


def test_parakeet_still_a_source_for_its_languages(tmp_path):
    out, _ = merged("uk", tmp_path)
    head = (out / "transcript.md").read_text()
    assert "Text sources: parakeet, whisper-large-v3, whisper-turbo" in head
