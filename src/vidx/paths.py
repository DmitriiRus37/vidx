"""Where vidx keeps its files, and which platform it runs on. The one module that knows the layout of the
package, of a checkout and of an installed copy."""
import ntpath
import os
import posixpath
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
LIB = PKG / "lib"  # scripts run by the engines' interpreters (and merge, frames, find)


def platform_of(name=None):
    """sys.platform → darwin | win32 | linux (any other POSIX system gets the Linux backends)."""
    name = name or sys.platform
    return "darwin" if name == "darwin" else "win32" if name.startswith("win") else "linux"


def checkout():
    """The source checkout this package runs from (pyproject.toml next to src/vidx), else None."""
    root = PKG.parent.parent
    return root if PKG.parent.name == "src" and (root / "pyproject.toml").is_file() else None


def data_dir(env, platform):
    """Per-user data folder of an installed copy."""
    if platform == "win32":
        return ntpath.join(env.get("LOCALAPPDATA") or ntpath.join(env.get("USERPROFILE", "C:\\"), "AppData", "Local"),
                           "vidx")
    home = env.get("HOME") or os.path.expanduser("~")
    if platform == "darwin":
        return posixpath.join(home, "Library", "Application Support", "vidx")
    xdg = env.get("XDG_DATA_HOME", "")
    return posixpath.join(xdg if xdg.startswith("/") else posixpath.join(home, ".local", "share"), "vidx")


def home(env=None, platform=None):
    """Where the venvs, models and compiled helpers live: VIDX_HOME, else the checkout vidx runs from (the
    layout of the bash version: .venv, .venv-giga, models/), else the per-user data folder."""
    env = os.environ if env is None else env
    if env.get("VIDX_HOME"):
        return env["VIDX_HOME"]
    root = checkout()
    return str(root) if root else data_dir(env, platform or platform_of())


def venv_python(venv, platform=None):
    """The interpreter of a venv: bin/python, or Scripts\\python.exe on Windows."""
    if (platform or platform_of()) == "win32":
        return ntpath.join(venv, "Scripts", "python.exe")
    return posixpath.join(venv, "bin", "python")
