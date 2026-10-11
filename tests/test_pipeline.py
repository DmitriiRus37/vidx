"""Pieces of a run (vidx.pipeline) that the end-to-end tests cannot reach: Vosk downloads, no symlinks
(Windows), a % in the temp dir, long OCR command lines."""
import json
import os
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

