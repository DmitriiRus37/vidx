"""Where vidx keeps its environments and models, per OS (vidx.paths)."""
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
