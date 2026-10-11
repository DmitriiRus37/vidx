"""The Python package: `python -m vidx`, the `vidx` console script and the checkout launcher."""
import os
import shutil
import subprocess
import tomllib

import pytest
from helpers import ROOT, VIDX, VIDX_CMD


def py_vidx(*args):
    return subprocess.run([*VIDX_CMD, *args], capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_console_script_declared():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["scripts"] == {"vidx": "vidx.cli:main"}
    assert project["requires-python"] == ">=3.11"


def test_importable_from_src():
    import vidx

    assert (ROOT / "src" / "vidx" / "lib" / "merge.py").exists()
    assert vidx.__file__.startswith(str(ROOT / "src" / "vidx"))


@pytest.mark.parametrize("args, code, first", [
    ([], 1, "vidx — extract pictures"), (["--help"], 0, "vidx — extract pictures"), (["help", "nope"], 0, "vidx —"),
    (["help", "run"], 0, "vidx <file|url> [options]"), (["help", "find"], 0, "vidx find <out-dir> <regex> [source]"),
    (["help", "setup"], 0, "vidx setup"), (["setup", "--help"], 0, "vidx setup"), (["find", "-h"], 0, "vidx find"),
    (["find", "only-one-arg"], 1, "vidx find"), (["x.mp4", "-j", "2", "--help"], 0, "vidx <file|url> [options]"),
])
def test_help(args, code, first):
    r = py_vidx(*args)
    assert r.returncode == code and r.stdout.startswith(first)


@pytest.mark.skipif(os.name == "nt" or not shutil.which("sh"), reason="the launcher is for POSIX checkouts")
def test_checkout_launcher_runs_the_package(tmp_path):
    """The repo-root vidx (POSIX sh) runs src/vidx, also through a symlink, with any Python it finds."""
    link = tmp_path / "vidx"
    link.symlink_to(VIDX)
    for exe in (VIDX, link):
        r = subprocess.run(["sh", str(exe), "help", "find"], capture_output=True, text=True, timeout=120, cwd=tmp_path)
        assert (r.returncode, r.stdout) == (0, py_vidx("help", "find").stdout)
    assert subprocess.run(["sh", "-n", str(VIDX)]).returncode == 0


@pytest.mark.skipif(os.name == "nt" or not shutil.which("sh"), reason="the launcher is for POSIX checkouts")
def test_launcher_ignores_the_current_directory(tmp_path):
    """A module planted in the working directory (a downloads folder) must not be imported: python -P."""
    (tmp_path / "vidx").mkdir()
    (tmp_path / "vidx" / "__init__.py").write_text("raise SystemExit('planted')\n", encoding="utf-8", newline="\n")
    r = subprocess.run(["sh", str(VIDX), "help"], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "planted" not in r.stderr
