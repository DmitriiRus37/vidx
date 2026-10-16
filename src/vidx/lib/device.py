"""The device an engine will use on this machine, for vidx setup's device report.

usage: device.py whisper|parakeet|gigaam      (whisper also stands for turbo and language ID)
Run by the engine venv's Python with -I -X utf8 (setup.isolated): -I keeps PYTHON* variables, the user site and
the current directory out, and also this script's directory, which it puts back itself to import its siblings.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import unix_stderr  # noqa: E402 — after the line above


def device(engine):
    if engine == "whisper":
        import whisper_fw

        return " ".join(whisper_fw.devices()[0])
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
