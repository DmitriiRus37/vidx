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
