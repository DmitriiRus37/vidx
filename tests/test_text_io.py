"""Text files are UTF-8 with LF line ends on every OS.

Windows opens text files in the ANSI code page (cp1252: Cyrillic cannot be written) and writes CRLF, so
every text open() in vidx and in its tests names its encoding, and every text write its newline."""
import ast
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


def test_every_text_open_names_encoding_and_newline():
    found = [p for path in SOURCES for p in problems(path)]
    assert found == []


def test_rule_catches_a_bare_open(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text('open("x")\nopen("y", "w", encoding="utf-8")\nopen("z", "rb")\nopen("v", encoding="utf-8")\n',
                   encoding="utf-8", newline="\n")
    assert list(problems(bad)) == ["bad.py:1: open() without encoding", "bad.py:2: text write without newline"]


def latin1_env():
    env = {**os.environ, "PYTHONUTF8": "0", "LC_ALL": "en_US.ISO8859-1", "LANG": "en_US.ISO8859-1"}
    probe = subprocess.run([sys.executable, "-c", "import locale; print(locale.getpreferredencoding(False))"],
                           env=env, capture_output=True, text=True)
    return env if probe.stdout.strip().upper().replace("-", "") in ("ISO88591", "LATIN1") else None


@pytest.mark.skipif(sys.platform == "win32" or not latin1_env(), reason="needs a Latin-1 locale (macOS has one)")
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
                       capture_output=True, text=True)
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
