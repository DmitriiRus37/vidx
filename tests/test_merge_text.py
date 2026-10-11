"""Text clean-up in the merge: joining chunks and capitalisation, for every script."""
import json

import pytest
from helpers import run_merge
from merge import capitalise


@pytest.mark.parametrize("raw, want", [
    ("привіт. їжак", "Привіт. Їжак"),
    ("über. äpfel", "Über. Äpfel"),
    ("добры дзень. ўсё добра", "Добры дзень. Ўсё добра"),
    ("hola. ¿qué tal? ¡éxito!", "Hola. ¿Qué tal? ¡Éxito!"),
    ("сегодня мы поговорим. дальше? да", "Сегодня мы поговорим. Дальше? Да"),
    ("今天我们聊天。星期六晴朗", "今天我们聊天。星期六晴朗"),  # no case: unchanged
    ("version 2. 3 items", "Version 2. 3 items"),
])
def test_capitalise(raw, want):
    assert capitalise(raw) == want


def write_asr(out, name, segs):
    (out / "asr").mkdir(parents=True, exist_ok=True)
    (out / "asr" / f"{name}.json").write_text(json.dumps({"segments": segs}, ensure_ascii=False))


def paragraph(out):
    return (out / "transcript.md").read_text().split("**[00:00:00]** ")[1].strip()


def test_zh_chunks_joined_without_spaces(tmp_path):
    out = tmp_path / "zh_vidx-out"
    write_asr(out, "whisper-large-v3", [{"start": 0, "end": 3, "text": "今天我们聊一聊天气。"},
                                        {"start": 3, "end": 6, "text": "星期六天气晴朗温暖。"}])
    run_merge(out, "--langs", "zh")
    assert paragraph(out) == "今天我们聊一聊天气。星期六天气晴朗温暖。"


def test_mixed_zh_en_chunks_keep_a_space_at_the_switch(tmp_path):
    out = tmp_path / "mix_vidx-out"
    write_asr(out, "whisper-large-v3@zh", [{"start": 0, "end": 3, "text": "今天我们聊一聊天气。"},
                                           {"start": 3, "end": 6, "text": "星期六天气晴朗温暖。"}])
    write_asr(out, "whisper-large-v3@en", [{"start": 6, "end": 9, "text": "Now in English."},
                                           {"start": 9, "end": 12, "text": "Second sentence."}])
    (out / "regions.json").write_text(json.dumps([{"start": 0, "end": 6, "lang": "zh"},
                                                  {"start": 6, "end": 12, "lang": "en"}]))
    run_merge(out, "--langs", "zh,en")
    assert paragraph(out) == "今天我们聊一聊天气。星期六天气晴朗温暖。 Now in English. Second sentence."


def test_mixed_en_zh_chunks_keep_a_space_at_the_switch(tmp_path):
    """The other direction: an English chunk, then Chinese (whose own joiner is "")."""
    out = tmp_path / "mix_vidx-out"
    write_asr(out, "whisper-large-v3@en", [{"start": 0, "end": 3, "text": "Now in English."}])
    write_asr(out, "whisper-large-v3@zh", [{"start": 6, "end": 9, "text": "今天我们聊一聊天气。"},
                                           {"start": 9, "end": 12, "text": "星期六天气晴朗温暖。"}])
    (out / "regions.json").write_text(json.dumps([{"start": 0, "end": 6, "lang": "en"},
                                                  {"start": 6, "end": 12, "lang": "zh"}]))
    run_merge(out, "--langs", "en,zh")
    assert paragraph(out) == "Now in English. 今天我们聊一聊天气。星期六天气晴朗温暖。"


def test_last_region_runs_to_the_end(tmp_path):
    """regions.json ends where the audio did when it was made; a chunk whose middle lies after that end
    still belongs to the last region."""
    out = tmp_path / "mix_vidx-out"
    write_asr(out, "whisper-large-v3@ru", [{"start": 0, "end": 4, "text": "Добрый вечер."}])
    write_asr(out, "whisper-large-v3@en", [{"start": 6, "end": 9, "text": "Now in English."},
                                           {"start": 9.5, "end": 11, "text": "One more sentence."}])
    (out / "regions.json").write_text(json.dumps([{"start": 0, "end": 5, "lang": "ru"},
                                                  {"start": 5, "end": 10, "lang": "en"}]))
    run_merge(out, "--langs", "ru,en")
    assert (out / "transcript.txt").read_text().splitlines()[-1] == "[00:00:09] One more sentence."
