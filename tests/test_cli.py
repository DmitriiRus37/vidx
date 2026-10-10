"""End-to-end runs of the bash vidx with fake engines: queue, stamps, resume, -j, failures."""
import pytest
from helpers import MEDIA, fake_calls, run_vidx

FIXTURE = MEDIA / "ru_slides.mp4"  # 4 s: Russian TTS + 2 slides
RU_JOBS = ["whisper large-v3 [ru]", "Vosk [ru] (CPU)", "frames + OCR", "GigaAM-v3 [ru]",
           "parakeet-tdt-0.6b-v3 [any]", "whisper turbo [ru]"]  # apple: no ru


def test_run_writes_transcript(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    out = tmp_path / "out"
    assert (out / "transcript.md").exists()
    assert "Fake gigaam ru" in (out / "transcript.txt").read_text()  # GigaAM is the ru base
    engines = [c["engine"] for c in fake_calls(tmp_path)]
    assert engines == ["whisper", "vosk", "ocr", "gigaam", "parakeet", "turbo"]  # queue order


def test_failed_job_exit_3_rest_merged(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", tmp_path=tmp_path, env={"VIDX_FAKE_FAIL": "gigaam"})
    assert r.returncode == 3
    assert "failed jobs: GigaAM-v3 [ru]" in r.stderr
    assert "Fake whisper ru" in (tmp_path / "out" / "transcript.txt").read_text()  # next base


def test_second_run_is_cached(tmp_path):
    assert run_vidx(FIXTURE, "-l", "ru", "-o", "out", tmp_path=tmp_path).returncode == 0
    n = len(fake_calls(tmp_path))
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert len(fake_calls(tmp_path)) == n  # no engine ran again
    for job in RU_JOBS:
        if job != "frames + OCR":  # checks its own two stamps, silently
            assert f"{job}: cached" in r.stderr


def test_option_change_reruns_only_affected_steps(tmp_path):
    assert run_vidx(FIXTURE, "-l", "ru", "-o", "out", tmp_path=tmp_path).returncode == 0
    n = len(fake_calls(tmp_path))
    gl = tmp_path / "g.txt"
    gl.write_text("prompt: Деплой\n")
    assert run_vidx(FIXTURE, "-l", "ru", "-o", "out", "-g", gl, tmp_path=tmp_path).returncode == 0
    assert sorted(c["engine"] for c in fake_calls(tmp_path)[n:]) == ["turbo", "whisper"]


def test_interrupted_job_reruns(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", tmp_path=tmp_path, env={"VIDX_FAKE_FAIL": "parakeet"})
    assert r.returncode == 3
    n = len(fake_calls(tmp_path))
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert [c["engine"] for c in fake_calls(tmp_path)[n:]] == ["parakeet"]


@pytest.mark.parametrize("jobs", [2, 3])
def test_jobs_limit(tmp_path, jobs):
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", "-j", jobs, tmp_path=tmp_path, env={"VIDX_FAKE_DELAY": "1.5"})
    assert r.returncode == 0, r.stderr
    calls = fake_calls(tmp_path)
    assert len(calls) == len(RU_JOBS)
    peak = max(sum(c["start"] <= t < c["end"] for c in calls) for t in (c["start"] for c in calls))
    assert peak == jobs
    assert (tmp_path / "out" / "logs" / "GigaAM-v3_ru_.log").exists()


def test_auto_language(tmp_path):
    r = run_vidx(FIXTURE, "-o", "out", tmp_path=tmp_path, env={"VIDX_FAKE_LANG": "en"})
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out" / "lang.txt").read_text().strip() == "en"
    assert "gigaam" not in [c["engine"] for c in fake_calls(tmp_path)]


@pytest.mark.parametrize("jobs", [1, 2])
def test_ctrl_c_stops_everything(tmp_path, jobs):
    """Ctrl-C in a terminal sends SIGINT to the whole foreground process group."""
    import os
    import signal
    import subprocess
    import time

    from helpers import BASH, VIDX, vidx_env

    p = subprocess.Popen([BASH, str(VIDX), str(FIXTURE), "-l", "ru", "-o", "out", "-j", str(jobs)], cwd=tmp_path,
                         env=vidx_env(tmp_path, {"VIDX_FAKE_DELAY": "60"}), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    pids = tmp_path / "fake.log.pids"
    deadline = time.time() + 30
    while not (pids.exists() and len(list(pids.iterdir())) >= jobs) and time.time() < deadline:
        time.sleep(0.1)
    assert pids.exists(), "no fake engine started"
    os.killpg(p.pid, signal.SIGINT)
    assert p.wait(timeout=5) == 130
    time.sleep(0.5)
    for f in pids.iterdir():
        with pytest.raises(ProcessLookupError):
            os.kill(int(f.name), 0)


def test_parakeet_not_queued_outside_its_languages(tmp_path):
    r = run_vidx(FIXTURE, "-l", "zh", "-o", "out", "-e", "whisper,parakeet", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert [c["engine"] for c in fake_calls(tmp_path)] == ["whisper", "ocr"]
    assert "parakeet: no 'zh', skipped" in r.stderr


def test_parakeet_queued_for_its_languages(tmp_path):
    r = run_vidx(FIXTURE, "-l", "uk", "-o", "out", "-e", "whisper,parakeet", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert [c["engine"] for c in fake_calls(tmp_path)] == ["whisper", "ocr", "parakeet"]


def test_apple_gets_explicit_locale_and_timeout(tmp_path):
    r = run_vidx(FIXTURE, "-l", "de", "-o", "out", "-e", "apple", tmp_path=tmp_path,
                 env={"VIDX_FAKE_APPLE_LOCALES": "de-AT de-CH de-DE en-US", "VIDX_APPLE_TIMEOUT": "7"})
    assert r.returncode == 0, r.stderr
    (call,) = [c for c in fake_calls(tmp_path) if c["engine"] == "apple"]
    assert call["args"][:3] == ["--timeout", "7", "de-DE"]
    assert (tmp_path / "out" / "asr" / "apple.json").exists()


def test_apple_skipped_when_locale_not_supported(tmp_path):
    r = run_vidx(FIXTURE, "-l", "de", "-o", "out", "-e", "apple", tmp_path=tmp_path,
                 env={"VIDX_FAKE_APPLE_LOCALES": "de-AT en-US"})  # de-AT is not de-DE
    assert r.returncode == 0, r.stderr
    assert "Apple SpeechTranscriber: no de-DE, skipped" in r.stderr
    assert not [c for c in fake_calls(tmp_path) if c["engine"] == "apple"]


def test_apple_timeout_marks_engine_failed_and_continues(tmp_path):
    r = run_vidx(FIXTURE, "-l", "de", "-o", "out", "-e", "apple,whisper", tmp_path=tmp_path,
                 env={"VIDX_FAKE_APPLE_LOCALES": "de-DE", "VIDX_FAKE_APPLE_EXIT": "4"})
    assert r.returncode == 3
    assert "model download for de-DE timed out" in r.stderr
    assert "failed jobs: Apple SpeechTranscriber [de]" in r.stderr
    assert "Fake whisper de" in (tmp_path / "out" / "transcript.txt").read_text()


def ocr_langs_of(tmp_path):
    (call,) = [c for c in fake_calls(tmp_path) if c["engine"] == "ocr"]
    return call["args"][0]


@pytest.mark.parametrize("lang, want", [("ru", "ru-RU,en-US"), ("ja", "ja-JP,en-US"), ("en", "en-US"),
                                        ("be", "ru-RU,uk-UA,en-US"), ("ko", "en-US")])
def test_ocr_follows_the_languages(tmp_path, lang, want):
    r = run_vidx(FIXTURE, "-l", lang, "-o", "out", "-e", "whisper", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert ocr_langs_of(tmp_path) == want


def test_ocr_for_mixed_run_is_the_union(tmp_path):
    r = run_vidx(FIXTURE, "-l", "zh,de", "-o", "out", "-e", "whisper", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert ocr_langs_of(tmp_path) == "zh-Hans,de-DE,en-US"


@pytest.mark.parametrize("lang, model", [("de", "vosk-model-de-0.21"), ("uk", "vosk-model-uk-v3"),
                                         ("zh", "vosk-model-cn-0.22"), ("ru", "vosk-model-ru-0.42")])
def test_vosk_model_from_the_table(tmp_path, lang, model):
    r = run_vidx(FIXTURE, "-l", lang, "-o", "out", "-e", "vosk", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    (call,) = [c for c in fake_calls(tmp_path) if c["engine"] == "vosk"]
    assert call["args"][0].endswith("/models/" + model)


def test_no_vosk_for_belarusian(tmp_path):
    r = run_vidx(FIXTURE, "-l", "be", "-o", "out", "-e", "vosk,whisper", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "vosk" not in [c["engine"] for c in fake_calls(tmp_path)]
    assert "Vosk: no model for 'be', skipped" in r.stderr
