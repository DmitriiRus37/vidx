"""Where vidx keeps its environments and models, per OS (vidx.paths)."""
import os
from pathlib import PureWindowsPath

import pytest

from vidx import paths


def test_venv_python_per_os():
    assert paths.venv_python("/h/.venv", "linux") == "/h/.venv/bin/python"
    assert paths.venv_python("/h/.venv", "darwin") == "/h/.venv/bin/python"
    win = paths.venv_python(r"C:\h\.venv", "win32")
    assert PureWindowsPath(win) == PureWindowsPath(r"C:\h\.venv\Scripts\python.exe")


def test_home_from_env_wins(tmp_path):
    assert paths.home({"VIDX_HOME": str(tmp_path)}, "linux") == str(tmp_path)


def test_home_is_the_checkout_when_running_from_one():
    assert paths.checkout() is not None  # the tests run from the repository
    assert paths.home({}, "darwin") == str(paths.checkout())


@pytest.mark.parametrize("platform, env, want", [
    ("linux", {"HOME": "/home/u"}, "/home/u/.local/share/vidx"),
    ("linux", {"HOME": "/home/u", "XDG_DATA_HOME": "/data"}, "/data/vidx"),
    ("linux", {"HOME": "/home/u", "XDG_DATA_HOME": "relative"}, "/home/u/.local/share/vidx"),  # XDG: absolute only
    ("darwin", {"HOME": "/home/mac"}, "/home/mac/Library/Application Support/vidx"),
    ("win32", {"LOCALAPPDATA": r"C:\Users\u\AppData\Local"}, r"C:\Users\u\AppData\Local\vidx"),
])
def test_data_dir_of_an_installed_copy(platform, env, want):
    assert paths.data_dir(env, platform) == want


def test_installed_copy_uses_the_data_dir(monkeypatch):
    monkeypatch.setattr(paths, "checkout", lambda: None)
    assert paths.home({"HOME": "/home/u"}, "linux") == "/home/u/.local/share/vidx"


def test_platform_names():
    assert paths.platform_of("linux") == "linux"
    assert paths.platform_of("darwin") == "darwin"
    assert paths.platform_of("win32") == "win32"
    assert paths.platform_of("cygwin") == "linux"  # POSIX paths and signals
    assert paths.platform_of("freebsd14") == "linux"  # POSIX without the macOS engines


def exe(folder, name):
    folder.mkdir(exist_ok=True)
    f = folder / name
    f.write_bytes(b"MZ")
    f.chmod(0o755)
    return str(f)


def test_windows_programs_are_not_taken_from_the_current_directory(tmp_path, monkeypatch):
    """Windows (CreateProcess, and shutil.which) looks in the current directory before PATH: an ffmpeg.exe lying in
    the folder vidx runs in (a download folder) would run instead of the installed one. paths.which looks in PATH
    only, and the programs are started by that absolute path."""
    cwd, tools = tmp_path / "Downloads", tmp_path / "tools"
    exe(cwd, "ffmpeg.exe")
    monkeypatch.chdir(cwd)
    env = {"PATH": os.pathsep.join(["", ".", str(tools)]), "PATHEXT": ".COM;.EXE"}
    assert paths.which("ffmpeg", "win32", env) is None  # only in the current directory: not found
    want = exe(tools, "ffmpeg.exe")
    assert os.path.samefile(paths.which("ffmpeg", "win32", env), want)  # PATHEXT's .EXE, any case
    assert os.path.samefile(paths.which("ffmpeg.exe", "win32", env), want)  # named with its extension


@pytest.mark.skipif(os.name == "nt", reason="shutil.which on Windows wants a PATHEXT extension")
def test_posix_which_is_shutils(tmp_path):
    tools = tmp_path / "bin"
    want = exe(tools, "ffmpeg")
    assert paths.which("ffmpeg", "linux", {"PATH": str(tools)}) == want
    assert paths.which("nothing-here", "linux", {"PATH": str(tools)}) is None


def test_windows_system_programs_by_full_path():
    win = paths.system_program("taskkill", "win32", {"SystemRoot": r"D:\Win"})
    assert PureWindowsPath(win) == PureWindowsPath(r"D:\Win\System32\taskkill.exe")
