"""End-to-end runs of vidx with fake engines: queue, stamps, resume, -j, failures."""
import sys
from pathlib import Path

import pytest
from helpers import MEDIA, fake_calls, needs_ffmpeg, run_vidx

from vidx.engines import Registry
from vidx.paths import platform_of

pytestmark = needs_ffmpeg

FIXTURE = MEDIA / "ru_slides.mp4"  # 4 s: Russian TTS + 2 slides
DARWIN = {"VIDX_FAKE_PLATFORM": "darwin"}  # the Apple engine exists only there
LID_MODEL = Registry(platform_of(), "/").lid.model  # the language-ID model of this platform
RU_JOBS = ["whisper large-v3 [ru]", "Vosk [ru] (CPU)", "frames + OCR", "GigaAM-v3 [ru]",
           "parakeet-tdt-0.6b-v3 [any]", "whisper turbo [ru]"]  # apple: no ru


def test_run_writes_transcript(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    out = tmp_path / "out"
    assert (out / "transcript.md").exists()
    assert "Fake gigaam ru" in (out / "transcript.txt").read_text(encoding="utf-8")  # GigaAM is the ru base
    engines = [c["engine"] for c in fake_calls(tmp_path)]
    assert engines == ["whisper", "vosk", "ocr", "gigaam", "parakeet", "turbo"]  # queue order


def test_failed_job_exit_3_rest_merged(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", tmp_path=tmp_path, env={"VIDX_FAKE_FAIL": "gigaam"})
    assert r.returncode == 3
    assert "failed jobs: GigaAM-v3 [ru]" in r.stderr
    assert "Fake whisper ru" in (tmp_path / "out" / "transcript.txt").read_text(encoding="utf-8")  # next base


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
    gl.write_text("prompt: Деплой\n", encoding="utf-8", newline="\n")
    assert run_vidx(FIXTURE, "-l", "ru", "-o", "out", "-g", gl, tmp_path=tmp_path).returncode == 0
    calls = fake_calls(tmp_path)[n:]
    assert sorted(c["engine"] for c in calls) == ["turbo", "whisper"]
    assert all("Деплой" in c["args"] for c in calls)  # also in a Latin-1 locale (jobs.os_arg)


@pytest.mark.parametrize("word", ["Деплой", "café"])
def test_the_glossary_prompt_reaches_whisper_as_written(tmp_path, word):
    """Also in a Latin-1 locale (ci latin-1): Cyrillic it cannot encode, é it can, and both must arrive as UTF-8."""
    gl = tmp_path / "g.txt"
    gl.write_text(f"prompt: {word}\n", encoding="utf-8", newline="\n")
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", "-e", "whisper", "--no-frames", "-g", gl, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert [c["args"][c["args"].index("--initial-prompt") + 1] for c in fake_calls(tmp_path)] == [word]


def test_interrupted_job_reruns(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", tmp_path=tmp_path, env={"VIDX_FAKE_FAIL": "parakeet"})
    assert r.returncode == 3
    n = len(fake_calls(tmp_path))
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert [c["engine"] for c in fake_calls(tmp_path)[n:]] == ["parakeet"]


@pytest.mark.parametrize("jobs", [2, 3])
def test_jobs_limit(tmp_path, jobs):
    """Each fake job takes 3 s, well above the queue's 1 s poll, so N jobs really overlap."""
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", "-j", jobs, tmp_path=tmp_path, env={"VIDX_FAKE_DELAY": "3"})
    assert r.returncode == 0, r.stderr
    calls = fake_calls(tmp_path)
    assert len(calls) == len(RU_JOBS)
    peak = max(sum(c["start"] <= t < c["end"] for c in calls) for t in (c["start"] for c in calls))
    assert peak == jobs
    assert (tmp_path / "out" / "logs" / "GigaAM-v3_ru_.log").exists()


def test_auto_language(tmp_path):
    r = run_vidx(FIXTURE, "-o", "out", tmp_path=tmp_path, env={"VIDX_FAKE_LANG": "en"})
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out" / "lang.txt").read_text(encoding="utf-8").strip() == "en"
    assert "gigaam" not in [c["engine"] for c in fake_calls(tmp_path)]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals; Windows: the Ctrl-Break test below")
@pytest.mark.parametrize("jobs", [1, 2])
def test_ctrl_c_stops_everything(tmp_path, jobs):
    """Ctrl-C in a terminal sends SIGINT to the whole foreground process group. vidx starts with SIGINT at its
    default, as from a terminal: a test run started in the background (`pytest &`) inherits it ignored, and an
    ignored SIGINT cannot be trapped, so neither vidx nor the engines would react."""
    import os
    import signal
    import subprocess
    import time

    from helpers import VIDX_CMD, vidx_env

    p = subprocess.Popen([*VIDX_CMD, str(FIXTURE), "-l", "ru", "-o", "out", "-j", str(jobs)], cwd=tmp_path,
                         env=vidx_env(tmp_path, {"VIDX_FAKE_DELAY": "60"}), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
    pids = tmp_path / "fake-calls.pids"
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


def start_vidx_posix(tmp_path, jobs, env):
    """vidx in its own session with SIGINT at its default (see test_ctrl_c_stops_everything); waits until `jobs`
    fake engines run. → (process, pid directory)."""
    import signal
    import subprocess
    import time

    from helpers import VIDX_CMD, vidx_env

    p = subprocess.Popen([*VIDX_CMD, str(FIXTURE), "-l", "ru", "-o", "out", "-j", str(jobs)], cwd=tmp_path,
                         env=vidx_env(tmp_path, {"VIDX_FAKE_DELAY": "60", **env}), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
    pids = tmp_path / "fake-calls.pids"
    deadline = time.time() + 30
    while not (pids.exists() and len(list(pids.iterdir())) >= jobs) and time.time() < deadline:
        time.sleep(0.1)
    assert pids.exists(), "no fake engine started"
    return p, pids


def kill_leftovers(p, pids):
    """A failed test must not leave vidx or a fake engine (in its own session) running."""
    import contextlib
    import os
    import signal

    with contextlib.suppress(OSError):
        os.killpg(p.pid, signal.SIGKILL)
    for f in pids.iterdir() if pids.exists() else []:
        with contextlib.suppress(OSError):
            os.killpg(int(f.name), signal.SIGKILL)
    p.wait(timeout=10)


def all_gone(pids):
    import os

    gone = []
    for f in pids.iterdir():
        try:
            os.kill(int(f.name), 0)
        except ProcessLookupError:
            gone.append(f.name)
    return len(gone) == len(list(pids.iterdir()))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("jobs", [1, 2])
def test_a_second_ctrl_c_does_not_stop_the_shutdown(tmp_path, jobs):
    """Ctrl-C pressed again while vidx kills the engines (an engine that ignores SIGTERM keeps it waiting
    KILL_WAIT_S) used to raise inside the kill: a traceback, exit 1, and the engine never got its SIGKILL."""
    import os
    import signal
    import time

    p, pids = start_vidx_posix(tmp_path, jobs, {"VIDX_FAKE_IGNORE_TERM": "1"})
    try:
        os.killpg(p.pid, signal.SIGINT)
        time.sleep(1)  # inside kill_all's wait for the engines that ignore SIGTERM
        os.kill(p.pid, signal.SIGINT)
        _, err = p.communicate(timeout=15)
        assert p.returncode == 130, err
        assert "Traceback" not in err and err.count("interrupted") == 1
        time.sleep(0.5)
        assert all_gone(pids)
    finally:
        kill_leftovers(p, pids)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_closing_the_terminal_stops_everything(tmp_path):
    """SIGHUP (the terminal closed) reaches vidx only: the engines run in sessions of their own, so vidx must kill
    them, as on Ctrl-C."""
    import os
    import signal
    import time

    p, pids = start_vidx_posix(tmp_path, 2, {})
    try:
        os.kill(p.pid, signal.SIGHUP)
        _, err = p.communicate(timeout=15)
        assert p.returncode == 130, err
        time.sleep(0.5)
        assert all_gone(pids)
    finally:
        kill_leftovers(p, pids)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console events")
@pytest.mark.parametrize("jobs", [1, 2])
def test_ctrl_break_stops_everything_on_windows(tmp_path, jobs):
    """Windows twin of the Ctrl-C test: vidx in its own process group gets CTRL_BREAK_EVENT (a Ctrl-C cannot be
    sent to a group), kills every engine tree and exits 130. Runs only on Windows (CI)."""
    import os
    import signal
    import subprocess
    import time

    import psutil
    from helpers import VIDX_CMD, vidx_env

    p = subprocess.Popen([*VIDX_CMD, str(FIXTURE), "-l", "ru", "-o", "out", "-j", str(jobs)], cwd=tmp_path,
                         env=vidx_env(tmp_path, {"VIDX_FAKE_DELAY": "60"}),
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8")
    pids = tmp_path / "fake-calls.pids"
    deadline = time.time() + 60
    while not (pids.exists() and len(list(pids.iterdir())) >= jobs) and time.time() < deadline:
        time.sleep(0.1)
    assert pids.exists(), "no fake engine started"
    os.kill(p.pid, signal.CTRL_BREAK_EVENT)
    assert p.wait(timeout=10) == 130
    time.sleep(0.5)
    assert not [f.name for f in pids.iterdir() if psutil.pid_exists(int(f.name))]


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
                 env={**DARWIN, "VIDX_FAKE_APPLE_LOCALES": "de-AT de-CH de-DE en-US", "VIDX_APPLE_TIMEOUT": "7"})
    assert r.returncode == 0, r.stderr
    (call,) = [c for c in fake_calls(tmp_path) if c["engine"] == "apple"]
    assert call["args"][:3] == ["--timeout", "7", "de-DE"]
    assert (tmp_path / "out" / "asr" / "apple.json").exists()


def test_apple_timeout_default(tmp_path):
    r = run_vidx(FIXTURE, "-l", "de", "-o", "out", "-e", "apple", tmp_path=tmp_path,
                 env={**DARWIN, "VIDX_FAKE_APPLE_LOCALES": "de-DE"})
    assert r.returncode == 0, r.stderr
    (call,) = [c for c in fake_calls(tmp_path) if c["engine"] == "apple"]
    assert call["args"][:2] == ["--timeout", "300"]
    help_run = run_vidx("help", "run", tmp_path=tmp_path, env=DARWIN).stdout  # the Apple engine: macOS help
    assert "VIDX_APPLE_TIMEOUT seconds (default 300, at most 86400)" in help_run


def test_apple_skipped_when_locale_not_supported(tmp_path):
    r = run_vidx(FIXTURE, "-l", "de", "-o", "out", "-e", "apple", tmp_path=tmp_path,
                 env={**DARWIN, "VIDX_FAKE_APPLE_LOCALES": "de-AT en-US"})  # de-AT is not de-DE
    assert r.returncode == 0, r.stderr
    assert "Apple SpeechTranscriber: no de-DE, skipped" in r.stderr
    assert not [c for c in fake_calls(tmp_path) if c["engine"] == "apple"]


def test_apple_timeout_marks_engine_failed_and_continues(tmp_path):
    r = run_vidx(FIXTURE, "-l", "de", "-o", "out", "-e", "apple,whisper", tmp_path=tmp_path,
                 env={**DARWIN, "VIDX_FAKE_APPLE_LOCALES": "de-DE", "VIDX_FAKE_APPLE_EXIT": "4"})
    assert r.returncode == 3
    assert "model download for de-DE timed out" in r.stderr
    assert "failed jobs: Apple SpeechTranscriber [de]" in r.stderr
    assert "Fake whisper de" in (tmp_path / "out" / "transcript.txt").read_text(encoding="utf-8")


def ocr_langs_of(tmp_path):
    (call,) = [c for c in fake_calls(tmp_path) if c["engine"] == "ocr"]
    return call["args"][0]


@pytest.mark.parametrize("lang, want", [("ru", "ru-RU,en-US"), ("ja", "ja-JP,en-US"), ("en", "en-US"),
                                        ("be", "ru-RU,uk-UA,en-US"), ("ko", "en-US")])
def test_ocr_follows_the_languages(tmp_path, lang, want):
    r = run_vidx(FIXTURE, "-l", lang, "-o", "out", "-e", "whisper", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert ocr_langs_of(tmp_path) == want


def test_ocr_for_mixed_scripts_one_group_per_script(tmp_path):
    """Vision reads only its first language's script: Latin/Cyrillic first, then one group per other script."""
    r = run_vidx(FIXTURE, "-l", "zh,de", "-o", "out", "-e", "whisper", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert ocr_langs_of(tmp_path) == "de-DE,en-US;zh-Hans,en-US"


@pytest.mark.parametrize("lang, model", [("de", "vosk-model-de-0.21"), ("uk", "vosk-model-uk-v3"),
                                         ("zh", "vosk-model-cn-0.22"), ("ru", "vosk-model-ru-0.42")])
def test_vosk_model_from_the_table(tmp_path, lang, model):
    r = run_vidx(FIXTURE, "-l", lang, "-o", "out", "-e", "vosk", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    (call,) = [c for c in fake_calls(tmp_path) if c["engine"] == "vosk"]
    assert Path(call["args"][0]).parts[-2:] == ("models", model)


def test_no_vosk_for_belarusian(tmp_path):
    r = run_vidx(FIXTURE, "-l", "be", "-o", "out", "-e", "vosk,whisper", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "vosk" not in [c["engine"] for c in fake_calls(tmp_path)]
    assert "Vosk: no model for 'be', skipped" in r.stderr


def regions_call(tmp_path):
    (call,) = [c for c in fake_calls(tmp_path) if c["engine"] == "regions"]
    return call["args"]


def test_mixed_regions_from_language_id_snapped_to_parakeet(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru,en", "-o", "out", "-e", "parakeet,whisper", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    args = regions_call(tmp_path)
    assert args[0] == LID_MODEL and args[1] == "ru,en"  # model first (lid_py)
    assert Path(args[-1]).parts[-2:] == ("asr", "parakeet.json")  # an OS path: backslashes on Windows
    assert (tmp_path / "out" / "regions.json").exists()


def test_mixed_split_survives_a_parakeet_failure(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru,en", "-o", "out", "-e", "parakeet,whisper", tmp_path=tmp_path,
                 env={"VIDX_FAKE_FAIL": "parakeet"})
    assert r.returncode == 3
    assert len(regions_call(tmp_path)) == 4  # no parakeet.json to snap to
    assert "whisper-large-v3@en" in (tmp_path / "out" / "transcript.md").read_text(encoding="utf-8")  # still mixed


def test_mixed_without_parakeet_engine_does_not_add_it(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru,en", "-o", "out", "-e", "whisper", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "parakeet" not in [c["engine"] for c in fake_calls(tmp_path)]
    assert len(regions_call(tmp_path)) == 4


UK_EN_DE = {"VIDX_FAKE_DATA": str(MEDIA.parent / "fake" / "uk_en_de"),
            "VIDX_FAKE_APPLE_LOCALES": "en-US de-DE fr-FR", **DARWIN}


def test_three_languages_engines_per_region_language(tmp_path):
    r = run_vidx(FIXTURE, "-l", "uk,en,de", "-o", "out", tmp_path=tmp_path, env=UK_EN_DE)
    assert r.returncode == 0, r.stderr
    calls = fake_calls(tmp_path)
    clips = {c["args"][c["args"].index("--language") + 1]: c["args"][c["args"].index("--clip-timestamps") + 1]
             for c in calls if c["engine"] == "whisper"}
    assert clips == {"uk": "0.0,1.5", "en": "1.5,3.0", "de": "3.0,4.06"}
    assert sorted(Path(c["args"][0]).name for c in calls if c["engine"] == "vosk") == [
        "vosk-model-de-0.21", "vosk-model-en-us-0.22", "vosk-model-uk-v3"]
    assert sorted(c["args"][2] for c in calls if c["engine"] == "apple") == ["de-DE", "en-US"]  # no uk
    assert [c["engine"] for c in calls].count("parakeet") == 1  # all three are parakeet languages
    assert "gigaam" not in [c["engine"] for c in calls]
    txt = (tmp_path / "out" / "transcript.txt").read_text(encoding="utf-8")
    assert txt.splitlines() == ["[00:00:00] Fake whisper uk.", "[00:00:01] Fake whisper en.",
                                "[00:00:03] Fake whisper de."]


def test_language_without_a_region_gets_no_engines(tmp_path):
    r = run_vidx(FIXTURE, "-l", "uk,en,de,fr", "-o", "out", "-e", "whisper,turbo,vosk,apple", tmp_path=tmp_path,
                 env=UK_EN_DE)
    assert r.returncode == 0, r.stderr
    calls = fake_calls(tmp_path)
    assert [c for c in calls if any(a in ("fr", "fr-FR") or "vosk-model-fr" in a for a in c["args"])] == []
    assert len(calls) == 1 + 3 + 3 + 2 + 3 + 1  # regions, whisper, vosk, apple (en, de), turbo, ocr
    for msg in ("whisper large-v3 [fr]: no fr regions, skipped", "Vosk [fr]: no fr regions, skipped",
                "Apple SpeechTranscriber [fr]: no fr regions, skipped"):
        assert msg in r.stderr


def test_parakeet_only_when_it_knows_every_language(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru,zh", "-o", "out", "-e", "parakeet,whisper", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "parakeet" not in [c["engine"] for c in fake_calls(tmp_path)]
    assert "parakeet: not every language of ru,zh is one of its 25, skipped" in r.stderr


def test_gigaam_skipped_without_ru_region(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "regions.json").write_text('[{"start": 0, "end": 4.06, "lang": "en"}]', encoding="utf-8", newline="\n")
    r = run_vidx(FIXTURE, "-l", "ru,en", "-o", "out", "-e", "gigaam,whisper", tmp_path=tmp_path,
                 env={"VIDX_FAKE_DATA": str(data)})
    assert r.returncode == 0, r.stderr
    assert "gigaam" not in [c["engine"] for c in fake_calls(tmp_path)]
    assert "GigaAM [ru]: no ru regions, skipped" in r.stderr


def test_outside_vidx_variables_do_not_leak_into_the_run(tmp_path, monkeypatch):
    """A VIDX_* variable set in the developer's shell must not change what the tests see."""
    monkeypatch.setenv("VIDX_FAKE_FAIL", "gigaam")
    monkeypatch.setenv("VIDX_APPLE_TIMEOUT", "7")
    r = run_vidx(FIXTURE, "-l", "ru", "-o", "out", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr


def test_repeated_language_counts_once(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru,en,ru", "-o", "out", "-e", "whisper", "-j", "2", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    langs = [c["args"][c["args"].index("--language") + 1] for c in fake_calls(tmp_path) if c["engine"] == "whisper"]
    assert sorted(langs) == ["en", "ru"]  # one writer per output file, even with -j 2 (finish order varies)
    assert "ru,en" in regions_call(tmp_path)


def test_same_language_twice_is_not_mixed(tmp_path):
    r = run_vidx(FIXTURE, "-l", "ru,ru", "-o", "out", "-e", "whisper", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "language: ru\n" in r.stderr
    assert "regions" not in [c["engine"] for c in fake_calls(tmp_path)]


def test_language_id_backend_is_one_swap_point(tmp_path):
    """Auto-detection and the region split both go through vidx's lid_py: one interpreter, one model id,
    passed first to every language-ID script."""
    r = run_vidx(FIXTURE, "-o", "out", "-e", "whisper", tmp_path=tmp_path, env={"VIDX_FAKE_LANG": "ru,en"})
    assert r.returncode == 0, r.stderr
    calls = {c["engine"]: c["args"] for c in fake_calls(tmp_path) if c["engine"] in ("detect_lang", "regions")}
    assert calls["detect_lang"][0] == calls["regions"][0] == LID_MODEL
    assert calls["detect_lang"][1].endswith("audio.wav")


def test_fake_hook_defaults_to_the_python_running_vidx(tmp_path):
    """Without VIDX_TEST_PYTHON the hook uses the Python that runs vidx (as its docstring says)."""
    import subprocess

    from helpers import VIDX_CMD, vidx_env

    env = vidx_env(tmp_path)
    del env["VIDX_TEST_PYTHON"]
    r = subprocess.run([*VIDX_CMD, str(FIXTURE), "-l", "ru", "-o", "out", "-e", "whisper", "--no-frames"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr


def test_apple_locales_asked_once_per_run(tmp_path):
    r = run_vidx(FIXTURE, "-l", "uk,en,de", "-o", "out", "-e", "apple", tmp_path=tmp_path, env=UK_EN_DE)
    assert r.returncode == 0, r.stderr
    assert [c["args"] for c in fake_calls(tmp_path, queries=True) if c["engine"] == "apple"].count(["--locales"]) == 1


def test_regions_redone_when_parakeet_snapping_appears_or_goes(tmp_path):
    """The regions stamp names the snapping (snap=parakeet|none), so adding or dropping parakeet redoes them."""
    def regions_runs(*engines):
        r = run_vidx(FIXTURE, "-l", "ru,en", "-o", "out", "-e", ",".join(engines), tmp_path=tmp_path)
        assert r.returncode == 0, r.stderr
        return [c["engine"] for c in fake_calls(tmp_path)].count("regions")

    assert regions_runs("whisper") == 1
    assert regions_runs("whisper", "parakeet") == 2  # now snapped to parakeet
    assert regions_runs("whisper", "parakeet") == 2  # same options: cached
    assert regions_runs("whisper") == 3  # parakeet gone: unsnapped again
    assert "snap=none" in (tmp_path / "out" / ".vidx" / "regions.json.params").read_text(encoding="utf-8")


def count_runs(tmp_path, *args, env=None):
    r = run_vidx(FIXTURE, *args, "-o", "out", tmp_path=tmp_path, env=env)
    assert r.returncode == 0, r.stderr
    return [c["engine"] for c in fake_calls(tmp_path)]


def test_another_language_id_model_redoes_detection_and_regions(tmp_path):
    """lang.txt was stamped "detect" and regions.json "<langs> lid snap=...": a different language-ID model (or
    parakeet model, for the snapping) kept the old result."""
    auto = ("-e", "whisper", "--no-frames")
    assert count_runs(tmp_path, *auto, env={"VIDX_FAKE_LANG": "ru,en"}).count("detect_lang") == 1
    assert count_runs(tmp_path, *auto, env={"VIDX_FAKE_LANG": "ru,en"}).count("detect_lang") == 1  # cached
    other = {"VIDX_FAKE_LANG": "ru,en", "VIDX_MODEL_TURBO": "another-turbo"}
    calls = count_runs(tmp_path, *auto, env=other)
    assert calls.count("detect_lang") == 2 and calls.count("regions") == 2
    snapped = ("-l", "ru,en", "-e", "whisper,parakeet", "--no-frames")
    assert count_runs(tmp_path, *snapped).count("regions") == 3
    assert count_runs(tmp_path, *snapped).count("regions") == 3  # cached
    assert count_runs(tmp_path, *snapped, env={"VIDX_MODEL_PARAKEET": "another-parakeet"}).count("regions") == 4


def test_another_ocr_backend_redoes_the_ocr(tmp_path):
    """The OCR stamp held only the languages: Apple Vision's ocr.jsonl was kept for a RapidOCR run and back."""
    args = ("-l", "ru", "-e", "whisper")
    assert count_runs(tmp_path, *args, env={"VIDX_FAKE_PLATFORM": "linux"}).count("ocr") == 1
    assert count_runs(tmp_path, *args, env={"VIDX_FAKE_PLATFORM": "linux"}).count("ocr") == 1  # cached
    assert count_runs(tmp_path, *args, env={"VIDX_FAKE_PLATFORM": "darwin"}).count("ocr") == 2
