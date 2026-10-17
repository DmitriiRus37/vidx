"""Loaded by vidx when VIDX_FAKE_ENGINES=1 (tests only): replaces every program that runs a model, downloads
or compiles with tests/fakes/engine.py, which writes canned output. The rest of vidx (options, output dir,
stamps, job queue, ffmpeg, frames.py, merge) runs for real. Environment:
  VIDX_TEST_PYTHON   Python with numpy + pillow, replaces vidx's helper venv (default: the Python running vidx)
  VIDX_FAKE_PLATFORM darwin | linux | win32: the engine registry of that platform (default: this one)
  VIDX_FAKE_LOG      a directory: one JSON file per fake call (engine, args, start, end); pids in <it>.pids/
  VIDX_FAKE_DELAY    seconds each fake engine takes (default 0)
  VIDX_FAKE_FAIL     comma list of fake engines that fail (whisper,turbo,parakeet,gigaam,...)
  VIDX_FAKE_LANG     what the fake language detection prints (default ru)
  VIDX_FAKE_DATA     dir with canned <output stem>.json / regions.json to return instead of stubs
  VIDX_FAKE_APPLE_LOCALES  locales the fake apple_asr --locales lists
  VIDX_FAKE_APPLE_EXIT     exit status of the fake apple_asr (4 = model download timed out)
  VIDX_FAKE_IGNORE_TERM    1: the fakes ignore SIGTERM (POSIX), so only the SIGKILL after KILL_WAIT_S stops them
"""
import sys
from pathlib import Path

ENGINE = str(Path(__file__).with_name("engine.py"))


def apply(rt):
    from vidx.engines import Registry

    py = rt.env.get("VIDX_TEST_PYTHON") or sys.executable

    def fake(name):  # -X utf8: what PYTHONUTF8=1 (ignored with -I) gives a real Python engine
        return [py, "-I", "-X", "utf8", ENGINE, name]

    platform = rt.env.get("VIDX_FAKE_PLATFORM")
    if platform:
        rt.platform = platform
    rt.registry = reg = Registry(rt.platform, rt.home, helper_python=py, env=rt.env)
    for name, engine in reg.engines.items():
        if engine:  # whisper and turbo are one CLI (mlx_whisper); the fake tells them apart by the model
            engine.program, engine.drop = fake("whisper" if name == "turbo" else name), None
    reg.lid_argv = lambda script, *args: [*fake(script), reg.lid.model, *map(str, args)]  # lib/<script>.py
    rt.build_swift = lambda name: True  # swiftc: nothing is compiled
    rt.fetch_vosk = lambda name, *a: bool(name)  # the model download: every named model "exists"
