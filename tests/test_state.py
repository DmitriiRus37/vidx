"""Output folder, step stamps and input tracking (vidx.state): the files and rules of the bash version."""
import os

import pytest

from vidx import log
from vidx.state import OutDir


def test_new_dir_gets_the_marker(tmp_path):
    out = OutDir.prepare(str(tmp_path / "out"), force=False)
    assert (tmp_path / "out" / ".vidx").is_dir()
    assert out.path == str(tmp_path / "out") and os.path.isabs(out.path)


def test_relative_dir_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert OutDir.prepare("./x_vidx-out", force=False).path == os.path.join(os.getcwd(), "x_vidx-out")


def test_foreign_dir_refused_and_untouched(tmp_path):
    (tmp_path / "mine").mkdir()
    (tmp_path / "mine" / "notes.txt").write_text("keep me", encoding="utf-8", newline="\n")
    with pytest.raises(log.Fatal, match="is not empty and was not made by vidx; pick another -o"):
        OutDir.prepare(str(tmp_path / "mine"), force=False)
    assert [p.name for p in (tmp_path / "mine").iterdir()] == ["notes.txt"]


def test_empty_foreign_dir_accepted(tmp_path):
    (tmp_path / "empty").mkdir()
    OutDir.prepare(str(tmp_path / "empty"), force=False)


def test_file_refused(tmp_path):
    (tmp_path / "f").write_text("x", encoding="utf-8", newline="\n")
    with pytest.raises(log.Fatal, match="exists and is not a directory"):
        OutDir.prepare(str(tmp_path / "f"), force=False)


def test_stamps(tmp_path):
    out = OutDir.prepare(str(tmp_path / "o"), force=False)
    f = tmp_path / "o" / "audio.wav"
    assert out.fresh(str(f), "16k-mono")  # missing output
    f.write_bytes(b"")
    assert out.fresh(str(f), "16k-mono")  # empty output counts as missing
    f.write_bytes(b"RIFF")
    assert out.fresh(str(f), "16k-mono")  # no stamp (an interrupted run)
    out.stamp(str(f), "16k-mono")
    assert (tmp_path / "o" / ".vidx" / "audio.wav.params").read_bytes() == b"16k-mono"  # printf '%s'
    assert not out.fresh(str(f), "16k-mono")
    assert out.fresh(str(f), "16k-stereo")  # other options
    assert not out.fresh(str(f))  # without params: only existence counts
    out.force = True
    assert out.fresh(str(f), "16k-mono") and out.fresh(str(f))


def test_stamp_compared_like_bash_command_substitution(tmp_path):
    """bash compared "$(cat stamp)" (trailing newlines dropped) with the params: a stamp written with a
    trailing newline still matches; params ending in spaces must match exactly."""
    out = OutDir.prepare(str(tmp_path / "o"), force=False)
    f = tmp_path / "o" / "asr.json"
    f.write_text("{}", encoding="utf-8", newline="\n")
    (tmp_path / "o" / ".vidx" / "asr.json.params").write_bytes(b"model ru  \n\n")
    assert not out.fresh(str(f), "model ru  ")
    assert out.fresh(str(f), "model ru")


def test_input_change_redoes_everything(tmp_path, capsys):
    out = OutDir.prepare(str(tmp_path / "o"), force=False)
    out.same_input("/a.mp4")
    assert not out.force
    out.same_input("/a.mp4")
    assert not out.force
    out.same_input("https://x")
    assert out.force
    assert "input changed (/a.mp4 → https://x): redoing everything" in capsys.readouterr().err
    assert (tmp_path / "o" / ".vidx" / "input").read_text(encoding="utf-8") == "https://x"


def test_find_source_ignores_ytdlp_leftovers(tmp_path):
    out = OutDir.prepare(str(tmp_path / "o"), force=False)
    o = tmp_path / "o"
    for name in ("source.f137.mp4", "source.mp4.part", "source.temp.mp4"):
        (o / name).write_bytes(b"x")
    assert out.find_source() is None
    (o / "source.webm").write_bytes(b"x")
    assert out.find_source() == str(o / "source.webm")


@pytest.mark.skipif(os.name == "nt", reason="symlink rights")
def test_find_source_skips_a_dangling_link(tmp_path):
    out = OutDir.prepare(str(tmp_path / "o"), force=False)
    os.symlink(tmp_path / "gone.mp4", tmp_path / "o" / "source.mp4")
    assert out.find_source() is None


def test_failed_list(tmp_path):
    out = OutDir.prepare(str(tmp_path / "o"), force=False)
    out.reset_failed()
    assert out.failed() == []
    out.add_failed("GigaAM-v3 [ru]")
    out.add_failed("frames + OCR")
    assert out.failed() == ["GigaAM-v3 [ru]", "frames + OCR"]
    assert (tmp_path / "o" / ".vidx" / "failed").read_bytes() == b"GigaAM-v3 [ru]\nframes + OCR\n"
