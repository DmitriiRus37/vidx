"""Text files are UTF-8 with LF line ends on every OS.

Windows opens text files in the ANSI code page (cp1252: Cyrillic cannot be written) and writes CRLF, so
every text open() in vidx and in its tests names its encoding, and every text write its newline. A program's
output read as text (subprocess text=True) is decoded in that code page, or a Latin-1 locale's encoding,
unless the call names one, so every such call does."""
import ast
import functools
import os
import subprocess
import sys

import pytest
from helpers import LIB, ROOT

SOURCES = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.py"))  # the tests run on Windows too


def calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            yield name, node


def mode_of(node):
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
        return node.args[1].value
    kw = {k.arg: k.value for k in node.keywords}
    return kw["mode"].value if "mode" in kw and isinstance(kw["mode"], ast.Constant) else "r"


def problems(path):
    where_of = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
    for name, node in calls(path):
        kw = {k.arg: k.value for k in node.keywords}
        where = f"{where_of}:{node.lineno}"
        if name == "open" and isinstance(node.func, ast.Name):
            mode = mode_of(node)
            if "b" in mode:
                continue
            if "encoding" not in kw:
                yield f"{where}: open() without encoding"
            if any(c in mode for c in "wax") and "newline" not in kw:
                yield f"{where}: text write without newline"
        elif name in ("read_text", "write_text") and "encoding" not in kw:
            yield f"{where}: {name}() without encoding"
        elif name == "write_text" and "newline" not in kw:
            yield f"{where}: write_text() without newline"
        elif name in SUBPROCESS and text_mode(kw) and "encoding" not in kw:
            yield f"{where}: subprocess text mode without encoding"


SUBPROCESS = ("run", "Popen", "check_output", "call", "check_call")


def text_mode(kw):
    """text=True, universal_newlines=True or errors=...: the output is decoded, in the locale's encoding (the
    code page on Windows) unless the call names one."""
    on = [kw[k] for k in ("text", "universal_newlines") if k in kw]
    return "errors" in kw or any(not (isinstance(v, ast.Constant) and not v.value) for v in on)


def test_every_text_open_and_subprocess_names_encoding_and_newline():
    found = [p for path in SOURCES for p in problems(path)]
    assert found == []


def test_rule_catches_a_bare_open(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text('open("x")\nopen("y", "w", encoding="utf-8")\nopen("z", "rb")\nopen("v", encoding="utf-8")\n'
                   'subprocess.run(["uv"], capture_output=True, text=True)\n'
                   'subprocess.Popen(["a"], universal_newlines=True)\n'
                   'subprocess.run(["b"], text=True, encoding="utf-8", errors="replace")\n'
                   'subprocess.run(["c"], text=False)\n'
                   'subprocess.check_output(["d"], errors="replace")\n'
                   'subprocess.run(["e"], capture_output=True)\n',
                   encoding="utf-8", newline="\n")
    assert list(problems(bad)) == ["bad.py:1: open() without encoding", "bad.py:2: text write without newline",
                                   "bad.py:5: subprocess text mode without encoding",
                                   "bad.py:6: subprocess text mode without encoding",
                                   "bad.py:9: subprocess text mode without encoding"]


LATIN1_LOCALES = ("en_US.ISO8859-1", "en_US.ISO-8859-1")  # macOS; glibc (`localedef -f ISO-8859-1`, the ci job)


@functools.cache
def latin1_env():
    for name in LATIN1_LOCALES:
        env = {**os.environ, "PYTHONUTF8": "0", "LC_ALL": name, "LANG": name}
        probe = subprocess.run([sys.executable, "-c", "import locale; print(locale.getpreferredencoding(False))"],
                               env=env, capture_output=True, text=True, encoding="utf-8")
        if probe.stdout.strip().upper().replace("-", "") in ("ISO88591", "LATIN1"):
            return env
    return None


@pytest.mark.skipif(sys.platform == "win32" or not latin1_env(),
                    reason="needs a Latin-1 locale (macOS has one; the ci job latin-1 makes one on Linux)")
def test_merge_in_a_latin1_locale(tmp_path):
    """The Windows code-page problem, reproduced with a Latin-1 locale: UTF-8 read as Latin-1 survives a plain
    copy, but not the Russian filler and capitalisation rules, which must see the real letters."""
    import json

    out = tmp_path / "ru_vidx-out"
    (out / "asr").mkdir(parents=True)
    seg = {"start": 0, "end": 5, "text": "э-э, мы начинаем. ммм, дальше"}
    (out / "asr" / "whisper-large-v3.json").write_text(json.dumps({"segments": [seg]}, ensure_ascii=False),
                                                      encoding="utf-8", newline="\n")
    r = subprocess.run([sys.executable, str(LIB / "merge.py"), str(out), "--langs", "ru"], env=latin1_env(),
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert (out / "transcript.txt").read_bytes() == "[00:00:00] Мы начинаем. Дальше\n".encode()


def test_unix_stdout_writes_utf8_and_lf_where_the_os_would_not():
    """A script's stdout on a Windows pipe: the code page and CRLF (vidx sets PYTHONUTF8=1 for its children, but
    a script run on its own, e.g. by a test, does not get it)."""
    import io

    import common

    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="\r\n")
    common.unix_stdout(stream)
    stream.write("ru,en\nПогода\n")
    stream.flush()
    assert stream.buffer.getvalue() == "ru,en\nПогода\n".encode()


def test_unix_stderr_writes_utf8_and_lf_when_redirected():
    """A script's diagnostics in a -j log or a pipe follow its data: UTF-8 with LF. A terminal keeps its own encoding
    and line ends (vidx re-encodes what it relays to a non-UTF-8 one)."""
    import io

    import common

    class Stream(io.TextIOWrapper):
        def __init__(self, encoding, newline, tty):
            super().__init__(io.BytesIO(), encoding=encoding, newline=newline)
            self.tty = tty

        def isatty(self):
            return self.tty

    pipe, tty = Stream("cp1252", "\r\n", False), Stream("latin-1", "\n", True)
    for s, text in ((pipe, "café → Погода\n"), (tty, "café\n")):
        common.unix_stderr(s)
        s.write(text)
        s.flush()
    assert pipe.buffer.getvalue() == "café → Погода\n".encode()
    assert tty.buffer.getvalue() == "café\n".encode("latin-1")


LOCALES = {"emulated": {**os.environ, "PYTHONIOENCODING": "latin-1", "PYTHONUTF8": "0"}, "real": latin1_env()}


@pytest.mark.parametrize("locale", ["emulated", "real"])
def test_lib_script_errors_are_utf8_with_lf_in_a_pipe(tmp_path, locale):
    """whisper_fw.py's error names the audio file: in a pipe it was the locale's encoding (Latin-1 here, the code page
    on Windows) and, on Windows, CRLF. "emulated": PYTHONIOENCODING on every OS; "real": a Latin-1 locale."""
    import wave

    env = LOCALES[locale]
    if env is None or (locale == "real" and sys.platform == "win32"):
        pytest.skip("needs a Latin-1 locale (macOS has one; the ci job latin-1 makes one on Linux)")
    with wave.open(str(tmp_path / "café.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)
    r = subprocess.run([sys.executable, str(LIB / "whisper_fw.py"), "café.wav", "--model", "tiny"], cwd=tmp_path,
                       env=env, capture_output=True, timeout=60)
    assert r.returncode == 1
    assert r.stderr == "whisper_fw: café.wav: 8000 Hz, expected 16000 (vidx writes 16 kHz mono)\n".encode()


def test_every_lib_script_sets_up_its_stderr():
    """Each script run by path calls common.unix_stderr (languages.py, which runs without lib/ on its path, sets UTF-8
    and LF on both streams itself)."""
    scripts = [p for p in sorted(LIB.glob("*.py")) if '__name__ == "__main__"' in p.read_text(encoding="utf-8")
               or p.name == "vosk_run.py"]
    missing = [p.name for p in scripts if "unix_stderr()" not in p.read_text(encoding="utf-8")]
    assert missing == ["languages.py"]
