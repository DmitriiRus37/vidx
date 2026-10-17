"""The device an engine will use on this machine, for vidx setup's device report.

usage: device.py whisper|parakeet|gigaam      (whisper also stands for turbo and language ID)
Run by the engine venv's Python with -I -X utf8 (setup.isolated): -I keeps PYTHON* variables, the user site and
the current directory out, and also this script's directory, which it puts back itself to import its siblings.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import unix_stderr  # noqa: E402 — after the line above


def cuda_libraries(platform=None):
    """The CUDA 12 library CTranslate2 (whisper, turbo, language ID) loads when a model first runs on the GPU
    (cuDNN is optional since CTranslate2 4.6.3)."""
    return ["cublas64_12.dll"] if (platform or sys.platform) == "win32" else ["libcublas.so.12"]


CUDA_LIBRARIES = cuda_libraries()


def load_library(name):
    """Load a shared library the way CTranslate2 does (Windows: the standard search, PATH included)."""
    import ctypes

    return ctypes.CDLL(name, winmode=0) if sys.platform == "win32" else ctypes.CDLL(name)


def whisper_device():
    """The device faster-whisper will run on: CTranslate2 counts the GPUs without loading the CUDA libraries, so
    they are loaded here; without them whisper_fw falls back to the CPU on the first clip."""
    import whisper_fw

    options = whisper_fw.devices()
    device, compute = options[0]
    if device == "cuda":
        missing = []
        for name in CUDA_LIBRARIES:
            try:
                load_library(name)
            except OSError:
                missing.append(name)
        if missing:
            why = f"CUDA libraries missing: {', '.join(missing)}"
            return f"{' '.join(options[1])} ({why})" if len(options) > 1 else f"cuda {compute} ({why}: it will fail)"
    return f"{device} {compute}"


def device(engine):
    if engine == "whisper":
        return whisper_device()
    if engine == "parakeet":
        import parakeet_onnx

        return parakeet_onnx.providers()[0]
    if engine == "gigaam":
        import gigaam_run
        import torch

        return gigaam_run.device(torch)
    sys.exit(f"device.py: unknown engine {engine!r} (whisper, parakeet or gigaam)")


if __name__ == "__main__":
    unix_stderr()
    print(device(sys.argv[1]))
