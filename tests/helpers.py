"""Shared test helpers: fixture paths, running lib scripts, golden files."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib"
FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN = ROOT / "tests" / "golden"
UPDATE_GOLDENS = os.environ.get("VIDX_UPDATE_GOLDENS") == "1"  # rewrite goldens instead of comparing


def out_dir_from(fixture, tmp_path, name="case"):
    """Copy a fixture output dir (asr/, regions.json...) to tmp_path/<name>_vidx-out."""
    out = tmp_path / f"{name}_vidx-out"
    shutil.copytree(FIXTURES / fixture, out)
    return out


def run_merge(out, *args):
    subprocess.run([sys.executable, str(LIB / "merge.py"), str(out), *args], check=True, capture_output=True)
    return out


def check_golden(case, out, files=("transcript.txt", "transcript.md")):
    """Compare out/<file> with tests/golden/<case>/<file>; VIDX_UPDATE_GOLDENS=1 rewrites them."""
    for name in files:
        got = (out / name).read_text()
        want = GOLDEN / case / name
        if UPDATE_GOLDENS:
            want.parent.mkdir(parents=True, exist_ok=True)
            want.write_text(got)
        assert got == want.read_text(), f"{case}/{name} differs from the golden file"


VIDX = ROOT / "vidx"
MEDIA = FIXTURES / "media"
BASH = "/bin/bash"  # macOS ships bash 3.2 there: the script must keep working with it


def vidx_env(tmp_path, env=None):
    e = dict(os.environ)
    e.update({"VIDX_FAKE_ENGINES": "1", "VIDX_TEST_PYTHON": sys.executable,
              "VIDX_FAKE_LOG": str(tmp_path / "fake.log"), "HF_HUB_OFFLINE": "1", "NO_COLOR": "1"})
    e.update(env or {})
    return e


def run_vidx(*args, tmp_path, env=None, timeout=120):
    """Run the bash vidx with the fake engines (tests/fakes/); every fake call is logged to
    tmp_path/fake.log (one JSON line per call)."""
    e = vidx_env(tmp_path, env)
    return subprocess.run([BASH, str(VIDX), *map(str, args)], cwd=tmp_path, env=e, capture_output=True,
                          text=True, timeout=timeout)


def fake_calls(tmp_path, queries=False):
    """The fake engine calls of the runs so far: [{"engine", "args", "start", "end"}]; without
    queries=True the `apple_asr --locales` lookups are left out."""
    import json

    log = tmp_path / "fake.log"
    if not log.exists():
        return []
    with open(log) as f:
        calls = [json.loads(line) for line in f]
    return [c for c in calls if queries or c["args"] != ["--locales"]]
