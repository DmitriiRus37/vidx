"""Belarusian without a recording (no TTS voice; a real clip is still missing): only whisper and turbo
know it, OCR uses ru + uk, no Vosk/Apple/parakeet/GigaAM."""
import json

import languages
from helpers import MEDIA, fake_calls, needs_ffmpeg, run_merge, run_vidx

BE_WHISPER = "Добры дзень. Сёння мы абмяркуем перазапуск сэрвісу і праверку графікаў пасля рэлізу."
BE_TURBO = "Добры дзень. Сёння мы абмяркуем перазапуск сэрвісу."


def test_table_row():
    be = languages.get("be")
    assert (be.script, be.apple_locale, be.vosk_model, be.parakeet) == ("cyrillic", None, None, False)
    assert languages.ocr_groups(["be"]) == [["ru-RU", "uk-UA", "en-US"]]


@needs_ffmpeg
def test_only_whisper_and_turbo_run(tmp_path):
    r = run_vidx(MEDIA / "ru_slides.mp4", "-l", "be", "-o", "out", tmp_path=tmp_path,
                 env={"VIDX_FAKE_PLATFORM": "darwin"})  # where Apple's engine exists and says why it skips
    assert r.returncode == 0, r.stderr
    calls = fake_calls(tmp_path)
    assert [c["engine"] for c in calls] == ["whisper", "ocr", "turbo"]
    assert all(c["args"][c["args"].index("--language") + 1] == "be" for c in calls if c["engine"] != "ocr")
    for skipped in ("Vosk: no model for 'be'", "GigaAM: Russian only", "parakeet: no 'be'",
                    "Apple SpeechTranscriber: no 'be'"):
        assert skipped in r.stderr


def be_out(tmp_path, sources):
    out = tmp_path / "be_vidx-out"
    (out / "asr").mkdir(parents=True)
    for name, text in sources.items():
        seg = {"start": 0.0, "end": 8.0, "text": text}
        (out / "asr" / f"{name}.json").write_text(json.dumps({"segments": [seg]}, ensure_ascii=False))
    return run_merge(out, "--langs", "be")


def test_merge_whisper_base_turbo_fallback(tmp_path):
    out = be_out(tmp_path, {"whisper-large-v3": BE_WHISPER, "whisper-turbo": BE_TURBO})
    assert (out / "transcript.txt").read_text() == f"[00:00:00] {BE_WHISPER}\n"
    out = be_out(tmp_path / "2", {"whisper-turbo": BE_TURBO})  # whisper failed
    assert (out / "transcript.txt").read_text() == f"[00:00:00] {BE_TURBO}\n"
