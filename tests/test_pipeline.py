"""Pieces of a run (vidx.pipeline) that the end-to-end tests cannot reach: Vosk downloads, no symlinks
(Windows), a % in the temp dir, long OCR command lines."""
import json
import os
import subprocess
import tempfile
import zipfile

import pytest
from helpers import MEDIA, needs_ffmpeg, vidx_env

from vidx import cli, pipeline


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)


def test_vosk_download_unpacks_into_place(tmp_path):
    src = tmp_path / "srv"
    src.mkdir()
    make_zip(src / "vosk-model-xx.zip", {"vosk-model-xx/am/final.mdl": "m", "vosk-model-xx/README": "r"})
    models = tmp_path / "models"
    assert pipeline.fetch_vosk("vosk-model-xx", str(models), src.as_uri() + "/")
    assert (models / "vosk-model-xx" / "am" / "final.mdl").read_text(encoding="utf-8") == "m"
    assert [p.name for p in models.iterdir()] == ["vosk-model-xx"]  # no temp dir left
    assert pipeline.fetch_vosk("vosk-model-xx", str(models), "file:///nowhere/")  # present: no download


@pytest.mark.parametrize("members", [{"../evil.txt": "x", "vosk-model-xx/a": "a"},
                                     {"vosk-model-xx/../../evil.txt": "x"}, {"other/a": "a"}])
def test_vosk_download_refuses_bad_archives(tmp_path, members, capsys):
    src = tmp_path / "srv"
    src.mkdir()
    make_zip(src / "vosk-model-xx.zip", members)
    models = tmp_path / "m" / "models"
    assert not pipeline.fetch_vosk("vosk-model-xx", str(models), src.as_uri() + "/")
    assert list(models.iterdir()) == [] and not (tmp_path / "m" / "evil.txt").exists()
    assert "Vosk model vosk-model-xx: download failed" in capsys.readouterr().err


def test_vosk_download_failure(tmp_path):
    assert not pipeline.fetch_vosk("vosk-model-xx", str(tmp_path / "models"), (tmp_path / "none").as_uri() + "/")
    assert not pipeline.fetch_vosk("", str(tmp_path / "models"))


def test_batches_keep_command_lines_short():
    files = [f"/very/long/path/frames/unique/{i:06d}.jpg" for i in range(3000)]
    got = list(pipeline.batches(files, limit=24000))
    assert [f for b in got for f in b] == files and len(got) > 1
    assert all(sum(len(f) + 1 for f in b) <= 24000 for b in got)


def run_in_process(tmp_path, monkeypatch, *args):
    for k, v in vidx_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    return cli.main([str(a) for a in args])


@needs_ffmpeg
def test_no_symlink_right_reads_the_input_in_place(tmp_path, monkeypatch, capsys):
    """Windows refuses symlinks without Developer Mode or admin rights: the run reads the input itself."""
    def refuse(*a, **k):
        raise OSError(1314, "A required privilege is not held by the client")

    monkeypatch.setattr(os, "symlink", refuse)
    assert run_in_process(tmp_path, monkeypatch, MEDIA / "ru_slides.mp4", "-l", "ru", "-o", "out", "-e", "whisper") == 0
    out = tmp_path / "out"
    assert not list(out.glob("source.*"))
    assert (out / "audio.wav").exists() and (out / "frames" / "frames.json").exists()
    assert (out / ".vidx" / "input").read_text(encoding="utf-8") == str((MEDIA / "ru_slides.mp4").resolve())
    assert "input read in place (no symlink allowed here)" in capsys.readouterr().err


@needs_ffmpeg
def test_percent_in_the_temp_dir(tmp_path, monkeypatch):
    """ffmpeg reads %06d in its output pattern: a % in the temp folder (a Windows user name) is escaped."""
    odd = tmp_path / "tmp%dir"
    odd.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(odd))
    args = (MEDIA / "ru_slides.mp4", "-l", "ru", "-o", "o%2d", "-e", "whisper")
    assert run_in_process(tmp_path, monkeypatch, *args) == 0
    frames = json.loads((tmp_path / "o%2d" / "frames" / "frames.json").read_text(encoding="utf-8"))
    assert frames and all(f["file"].endswith(".jpg") for f in frames)


def one_flash_video(path):
    """4 s of white at 25 fps; frame 50 (t = 2 s) alone shows a small black box: a scene change far below
    --scene 0.3, but more than 1 % of the dedup thumbnail."""
    box = "drawbox=x=100:y=100:w=300:h=100:color=black:t=fill:enable='eq(n,50)'"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=white:s=1280x720:r=25:d=4",
                    "-vf", box, "-c:v", "mpeg4", "-q:v", "2", str(path)], check=True)
    return path


@needs_ffmpeg
@pytest.mark.parametrize("extra, times", [((), [0.0]), (("--all-frames",), [0.0, 2.0])])
def test_all_frames_catches_a_single_frame(tmp_path, monkeypatch, extra, times):
    """--all-frames looks at every frame: the one-frame box survives dedup, the white after it does not."""
    video = one_flash_video(tmp_path / "flash.mp4")
    args = (video, "-o", "out", "--no-asr", "--no-ocr", "--no-subs", *extra)
    assert run_in_process(tmp_path, monkeypatch, *args) == 0
    frames = json.loads((tmp_path / "out" / "frames" / "frames.json").read_text(encoding="utf-8"))
    assert [f["t"] for f in frames] == times


def spy_spawn(monkeypatch):
    from vidx import jobs

    seen, real = [], jobs.spawn
    monkeypatch.setattr(jobs, "spawn", lambda argv, *a, **k: seen.append(list(argv)) or real(argv, *a, **k))
    return seen


@needs_ffmpeg
def test_the_glossary_prompt_goes_to_whisper_as_text(tmp_path, monkeypatch):
    """The prompt is text, not a path: jobs.spawn sends it as UTF-8 in every locale (jobs.Text)."""
    from vidx import jobs

    seen = spy_spawn(monkeypatch)
    gl = tmp_path / "g.txt"
    gl.write_text("prompt: café\n", encoding="utf-8", newline="\n")
    assert run_in_process(tmp_path, monkeypatch, MEDIA / "ru_slides.mp4", "-l", "ru", "-o", "out", "-e", "whisper",
                          "--no-frames", "-g", gl) == 0
    prompts = [a for argv in seen for a in argv if str(a).startswith("--initial-prompt")]
    assert prompts == ["--initial-prompt=café"] and type(prompts[0]) is jobs.Text


def test_the_url_goes_to_ytdlp_as_text(tmp_path, monkeypatch):
    """A URL is text (yt-dlp reads its argv as UTF-8 in vidx's PYTHONUTF8=1), not a file name."""
    from vidx import jobs
    from vidx.log import Fatal
    from vidx.options import parse_run

    calls = []
    monkeypatch.setattr(jobs, "capture", lambda argv, **k: calls.append(argv) or (1, ""))
    monkeypatch.setattr(pipeline.Runtime, "which", lambda self, tool: tool)
    run = pipeline.Run(parse_run(["https://example.com/café"]), pipeline.Runtime(env={}))
    with pytest.raises(Fatal, match="could not read the video id"):
        run.download()
    assert calls[0][-1] == "https://example.com/café" and type(calls[0][-1]) is jobs.Text


def test_vosk_download_gives_up_on_a_silent_server(tmp_path, monkeypatch, capsys):
    """urlopen without a timeout waits forever for a server that accepts the connection and never answers (a
    stuck proxy): vidx setup or a run hung on the Vosk download."""
    import socket
    import time

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)  # never accepted: the connection is made, no answer ever comes
    monkeypatch.setattr(pipeline, "DOWNLOAD_TIMEOUT_S", 1)
    try:
        t0 = time.monotonic()
        url = f"http://127.0.0.1:{server.getsockname()[1]}/"
        assert not pipeline.fetch_vosk("vosk-model-xx", str(tmp_path / "models"), url)
        assert time.monotonic() - t0 < 10
    finally:
        server.close()
    assert "no answer for 1 s" in capsys.readouterr().err
