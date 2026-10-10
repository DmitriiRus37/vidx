"""CLI contract: option validation and refusals. Table-driven, so the same table can later run
against a Python rewrite of the entry point. Each row: args, expected exit status, stderr part."""
import pytest
from helpers import MEDIA, run_vidx

AV = str(MEDIA / "ru_slides.mp4")
SILENT = str(MEDIA / "noaudio.mp4")
CONTRACT = {
    "no_args": ([], 1, "USAGE"),
    "help": (["help"], 0, ""),
    "help_run": (["help", "run"], 0, ""),
    "missing_input": (["nope.mp4"], 1, "no such file: nope.mp4"),
    "missing_glossary": ([AV, "-g", "nope.txt"], 1, "no such glossary: nope.txt"),
    "bad_cut_format": ([AV, "--cut", "36:30"], 1, "--cut needs START-END"),
    "bad_cut_order": ([AV, "--cut", "50:00-36:30"], 1, "start must be before end"),
    "unknown_engine": ([AV, "-e", "whisper,foo"], 1, "unknown engine 'foo'"),
    "bad_lang": ([AV, "-l", "Russian"], 1, "--lang must be auto or a list"),
    "bad_jobs": ([AV, "-j", "0"], 1, "--jobs must be a positive number"),
    "bad_number": ([AV, "--scene", "high"], 1, "--scene must be a number"),
    "missing_value": ([AV, "-o"], 1, "option -o needs a value"),
    "unknown_option": ([AV, "--frobnicate"], 1, "unknown option --frobnicate"),
    "no_audio": ([SILENT, "-o", "out"], 1, "no audio track"),
    "no_audio_no_asr": ([SILENT, "-o", "out", "--no-asr"], 0, ""),
}


@pytest.mark.parametrize("case", sorted(CONTRACT))
def test_contract(case, tmp_path):
    args, code, msg = CONTRACT[case]
    r = run_vidx(*args, tmp_path=tmp_path)
    assert r.returncode == code, r.stderr
    assert msg in r.stderr + r.stdout


def test_foreign_out_dir_refused(tmp_path):
    (tmp_path / "mine").mkdir()
    (tmp_path / "mine" / "notes.txt").write_text("keep me")
    r = run_vidx(AV, "-o", "mine", tmp_path=tmp_path)
    assert r.returncode == 1
    assert "was not made by vidx" in r.stderr
    assert sorted(p.name for p in (tmp_path / "mine").iterdir()) == ["notes.txt"]


def test_out_path_is_a_file(tmp_path):
    (tmp_path / "f").write_text("x")
    r = run_vidx(AV, "-o", "f", tmp_path=tmp_path)
    assert r.returncode == 1
    assert "is not a directory" in r.stderr


def test_different_input_redoes_everything(tmp_path):
    assert run_vidx(AV, "-l", "ru", "-o", "out", tmp_path=tmp_path).returncode == 0
    r = run_vidx(SILENT, "-o", "out", "--no-asr", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "input changed" in r.stderr
