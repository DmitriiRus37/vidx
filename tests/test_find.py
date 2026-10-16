"""vidx find: the hits are UTF-8 with LF wherever they go, like vidx's other output."""
import io
import json
import os
import subprocess
import sys

import find
from helpers import VIDX_CMD, out_dir_from, run_vidx

HIT = ("00:00:01  parakeet          Сегодня мы готовим овощной суп на ужин.\n"
       "00:00:34  parakeet          Подавать суп лучше горячим со сметаной и свежим хлебом.\n")


class Stream(io.TextIOWrapper):
    def __init__(self, encoding, newline=None, tty=False):
        super().__init__(io.BytesIO(), encoding=encoding, newline=newline)
        self.tty = tty

    def isatty(self):
        return self.tty


def test_hits_are_utf8_with_lf_on_a_windows_pipe(tmp_path, monkeypatch):
    """find.py's stdout on a Windows pipe or file: the code page and CRLF unless the script sets its own."""
    out = out_dir_from("merge/ru", tmp_path)
    stream = Stream("cp1252", "\r\n")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "argv", ["find.py", str(out), "суп", "parakeet"])
    find.main()
    sys.stdout.flush()
    assert stream.buffer.getvalue() == HIT.encode()


def test_vidx_find_in_a_non_utf8_locale(tmp_path):
    """A Latin-1 locale (emulated on every OS by PYTHONIOENCODING, which vidx's children inherit). The regex is
    "суп" in ASCII: a real Latin-1 locale (ci latin-1) cannot put Cyrillic on vidx's command line."""
    out = out_dir_from("merge/ru", tmp_path)
    r = subprocess.run([*VIDX_CMD, "find", str(out), r"\u0441\u0443\u043f", "parakeet"], capture_output=True,
                       timeout=60, env={**os.environ, "PYTHONIOENCODING": "latin-1", "PYTHONUTF8": "0"})
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert r.stdout == HIT.encode()


def test_vidx_find_shows_hits_in_the_terminals_encoding(tmp_path, monkeypatch):
    """The hits go through vidx's own stdout, so a terminal gets them as it gets vidx's messages (log.setup_streams):
    in its own encoding, with "?" for what it has no code for."""
    from vidx import cli, log

    out = out_dir_from("merge/ru", tmp_path)
    tty = Stream("latin-1", "\n", tty=True)  # its line ends are its own: LF here, on Windows too
    log.setup_streams([tty])
    monkeypatch.setattr(sys, "stdout", tty)
    assert cli.find([str(out), "суп", "parakeet"]) == 0
    tty.flush()
    assert tty.buffer.getvalue() == HIT.encode("latin-1", "replace")


def cafe_out(tmp_path):
    out = tmp_path / "fr_vidx-out"
    (out / "asr").mkdir(parents=True)
    seg = {"start": 61, "end": 63, "text": "Un café, s'il vous plaît."}
    (out / "asr" / "whisper-large-v3.json").write_text(json.dumps({"segments": [seg]}, ensure_ascii=False),
                                                      encoding="utf-8", newline="\n")
    return out


def test_vidx_find_matches_a_regex_with_letters_the_locale_can_encode(tmp_path):
    """In a Latin-1 locale (ci latin-1) "café" reaches vidx as Latin-1 bytes; find.py (UTF-8 mode) must get it as
    UTF-8, or it reads an undecodable byte for é and finds nothing."""
    r = run_vidx("find", cafe_out(tmp_path), "café", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "00:01:01  whisper-large-v3  Un café, s'il vous plaît.\n"


def test_vidx_find_passes_the_regex_as_text(tmp_path, monkeypatch):
    """The regex is text and the folder a path: jobs.spawn encodes them differently in a non-UTF-8 locale."""
    from vidx import cli, jobs

    seen, real = [], jobs.spawn
    monkeypatch.setattr(jobs, "spawn", lambda argv, *a, **k: seen.append(list(argv)) or real(argv, *a, **k))
    monkeypatch.setattr(sys, "stdout", Stream("utf-8"))
    assert cli.find([str(cafe_out(tmp_path)), "café", "whisper-large-v3"]) == 0
    folder, regex, source = seen[0][2:]
    assert type(regex) is jobs.Text and type(folder) is str and type(source) is str
