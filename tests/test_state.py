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


def test_jobs_failing_together_are_all_listed(tmp_path, monkeypatch):
    """With -j N, jobs fail in worker threads at the same moment. On Windows an append is a seek to the end and
    then a write, so two appends at once land at the same offset and one label is lost (exit 0 instead of 3);
    that append is emulated here, on every OS."""
    import builtins
    import threading
    import time

    from vidx import state

    real_open = builtins.open

    class SeekThenWrite:
        def __init__(self, path, encoding):
            self.path, self.encoding = path, encoding

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, text):
            end = os.path.getsize(self.path)
            time.sleep(0.05)  # the other threads find the same end meanwhile
            with real_open(self.path, "r+b") as f:
                f.seek(end)
                f.write(text.encode(self.encoding))

    def windows_open(path, mode="r", encoding=None, newline=None):
        return SeekThenWrite(path, encoding) if "a" in mode else real_open(path, mode, encoding=encoding,
                                                                             newline=newline)

    out = OutDir.prepare(str(tmp_path / "o"), force=False)
    out.reset_failed()
    monkeypatch.setattr(state, "open", windows_open, raising=False)
    start = threading.Barrier(4)

    def fail(label):
        start.wait()
        out.add_failed(label)

    threads = [threading.Thread(target=fail, args=(f"job{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(out.failed()) == ["job0", "job1", "job2", "job3"]


def test_an_undecodable_input_name_is_remembered(tmp_path, capsys):
    """Linux, UTF-8 locale, a file name in Latin-1 bytes: Python gives it as "caf\\udce9.mp4" (os.fsdecode, the
    surrogateescape of the undecodable byte), which a strict UTF-8 write of .vidx/input could not store."""
    name = os.path.join(str(tmp_path), b"caf\xe9.mp4".decode("utf-8", "surrogateescape"))  # os.fsdecode on Linux
    out = OutDir.prepare(str(tmp_path / "o"), force=False)
    out.same_input(name)
    assert (tmp_path / "o" / ".vidx" / "input").read_bytes().endswith(b"caf\xe9.mp4")  # the name's own bytes
    out.same_input(name)
    assert not out.force and "input changed" not in capsys.readouterr().err
    out.same_input(os.path.join(str(tmp_path), "other.mp4"))
    assert out.force and "input changed" in capsys.readouterr().err
