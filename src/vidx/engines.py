"""Which program runs each engine, per platform.

The pipeline builds the same arguments for an engine on every OS (the command line of today's macOS tool:
mlx_whisper, parakeet-mlx, the Swift helpers); a backend is any program that accepts them and writes the
same JSON. So this table is all that differs between platforms:

  engine     arguments                                          darwin              linux / win32
  whisper    <audio> --model M -o DIR --output-name STEM ...    mlx_whisper         lib/whisper_fw.py
  turbo      (same as whisper, turbo model)                     mlx_whisper         lib/whisper_fw.py
  parakeet   <audio> --model M --output-format json ...         parakeet-mlx        lib/parakeet_onnx.py
  gigaam     <audio> <out.json> [regions.json]                  lib/gigaam_run.py   lib/gigaam_run.py
  vosk       <model dir> <audio> <out.json>                     lib/vosk_run.py     lib/vosk_run.py
  apple      --timeout S <locale> <audio> <out.json>            bin/apple_asr       not available
  ocr        <groups> <image>...                                bin/ocr             lib/ocr_rapid.py
  lid        lib/<script>.py <model> <args...>                  mlx-whisper python  .venv-whisper python

Output stems and JSON shapes are the same everywhere, so merge.py does not know which backend ran.
tests/fakes/hook.py swaps every program for tests/fakes/engine.py.
"""
import os
import subprocess
from dataclasses import dataclass

from vidx import paths

ENGINES = ("whisper", "parakeet", "gigaam", "turbo", "apple", "vosk")  # vidx -e names, help order

# model ids (the stamps contain them, so a model change reruns the step)
MLX = {"whisper": "mlx-community/whisper-large-v3-mlx", "turbo": "mlx-community/whisper-large-v3-turbo",
       "parakeet": "mlx-community/parakeet-tdt-0.6b-v3"}
PORTABLE = {"whisper": "large-v3", "turbo": "large-v3-turbo", "parakeet": "nemo-parakeet-tdt-0.6b-v3"}
GIGAAM_MODEL = "v3_e2e_rnnt"


@dataclass
class Engine:
    name: str
    model: str
    program: list  # argv prefix; the engine's arguments follow
    runs_on: str = ""  # for the help text
    drop: str | None = None  # stderr lines containing this are not shown

    def argv(self, *args):
        return [*self.program, *map(str, args)]


@dataclass
class LanguageID:
    """lib/detect_lang.py, regions.py and lid.py, run by the language-ID backend's interpreter."""
    model: str
    python: list
    drop: str | None = "Fetching"  # Hugging Face's download progress

    def argv(self, script, *args):
        return [*self.python, str(paths.LIB / f"{script}.py"), self.model, *map(str, args)]


class Registry:
    """The engines of one platform; home holds the venvs (paths.home())."""

    def __init__(self, platform, home, helper_python=None):
        self.platform, self.home = platform, home
        self.helper_python = helper_python or paths.venv_python(os.path.join(home, ".venv"), platform)
        self.bin = os.path.join(home, "bin")  # compiled Swift helpers (macOS)
        self.models = os.path.join(home, "models")
        self._uv_tool_dir = None
        lib = paths.LIB
        venv = lambda name: paths.venv_python(os.path.join(home, name), platform)  # noqa: E731
        common = {
            "gigaam": Engine("gigaam", GIGAAM_MODEL, [venv(".venv-giga"), str(lib / "gigaam_run.py")]),
            "vosk": Engine("vosk", "", [self.helper_python, str(lib / "vosk_run.py")], "CPU"),
        }
        if platform == "darwin":
            self.engines = {
                "whisper": Engine("whisper", MLX["whisper"], ["mlx_whisper"], "GPU (MLX)"),
                "turbo": Engine("turbo", MLX["turbo"], ["mlx_whisper"], "GPU (MLX)"),
                "parakeet": Engine("parakeet", MLX["parakeet"], ["parakeet-mlx"], "GPU (MLX)"),
                "apple": Engine("apple", "", [os.path.join(self.bin, "apple_asr")], "ANE"),
                "ocr": Engine("ocr", "Apple Vision", [os.path.join(self.bin, "ocr")]),
                **common,
            }
            self.lid = LanguageID(MLX["turbo"], [])  # the interpreter is resolved on first use (uv tool dir)
        else:
            whisper_py = venv(".venv-whisper")
            fw = [whisper_py, str(lib / "whisper_fw.py")]
            self.engines = {
                "whisper": Engine("whisper", PORTABLE["whisper"], fw, "CUDA or CPU (faster-whisper)"),
                "turbo": Engine("turbo", PORTABLE["turbo"], fw, "CUDA or CPU (faster-whisper)"),
                "parakeet": Engine("parakeet", PORTABLE["parakeet"],
                                   [venv(".venv-onnx"), str(lib / "parakeet_onnx.py")], "CUDA/DirectML/CPU (onnx-asr)"),
                "apple": None,  # Apple's SpeechTranscriber: macOS only
                "ocr": Engine("ocr", "RapidOCR", [whisper_py, str(lib / "ocr_rapid.py")]),
                **common,
            }
            self.lid = LanguageID(PORTABLE["turbo"], [whisper_py])

    def get(self, name):
        """The engine, or None if this platform has no backend for it."""
        return self.engines.get(name)

    def lid_argv(self, script, *args):
        if not self.lid.python:  # macOS: mlx-whisper's own interpreter (a uv tool)
            self.lid.python = [paths.venv_python(os.path.join(self.uv_tool_dir(), "mlx-whisper"), self.platform)]
        return self.lid.argv(script, *args)

    def uv_tool_dir(self):
        if self._uv_tool_dir is None:
            r = subprocess.run(["uv", "tool", "dir"], capture_output=True, text=True)
            self._uv_tool_dir = r.stdout.strip()
        return self._uv_tool_dir
