"""Pinned tools and environments that `vidx setup` installs, per platform. Versions were tested together
(macOS: the bash version's pins; Linux: a container run, docs/plans/2026-10-11-porting-analysis.md)."""

# uv tools. yt-dlp stays unpinned on purpose: YouTube breaks old versions.
TOOLS = ["mlx-whisper==0.4.3", "parakeet-mlx==0.5.3", "yt-dlp[default]"]
PORTABLE_TOOLS = ["yt-dlp[default]"]
# .venv: frames, merge, find, Vosk; psutil kills process trees on Windows
HELPER_PKGS = ["vosk==0.3.44", "pillow==12.3.0", "numpy==2.4.6", "psutil==7.2.2"]
# .venv-giga: torch first, from PyTorch's own index on Linux/Windows (CPU or CUDA 12.6 builds), then the rest
# from PyPI. GigaAM from the archive of a pinned commit: no git needed.
TORCH = ["torch==2.14.1", "torchaudio==2.11.0"]
TORCH_INDEX = {"cpu": "https://download.pytorch.org/whl/cpu", "cuda": "https://download.pytorch.org/whl/cu126"}
GIGAAM = ("gigaam[torch] @ https://github.com/salute-developers/GigaAM/archive/"
          "7447938d791c4f3e643386ee22c33777004293a5.zip")
GIGA_PKGS = [GIGAAM, "soundfile==0.14.0", "numpy==2.4.6"]
# .venv-whisper (Linux, Windows): whisper, turbo, language ID, OCR. faster-whisper needs the CPU onnxruntime
# (its VAD), which is why the GPU onnxruntime of parakeet lives in its own venv.
WHISPER_PKGS = ["faster-whisper==1.2.1", "ctranslate2==4.8.2", "av==19.0.1", "onnxruntime==1.31.0",
                "rapidocr[rtl]==3.10.0", "numpy==2.4.6", "pillow==12.3.0"]
# .venv-onnx (Linux, Windows): parakeet. One onnxruntime flavour; onnxruntime-gpu >= 1.27 needs CUDA 13,
# CTranslate2 is built for CUDA 12.
ONNX_PKGS = ["onnx-asr[hub]==0.12.0", "numpy==2.4.6"]
ONNXRUNTIME = {"cpu": "onnxruntime==1.31.0", "cuda": "onnxruntime-gpu>=1.20,<1.27",
               "directml": "onnxruntime-directml==1.24.4"}
# Python of the venvs: the code runs on 3.11+; GigaAM pins onnxruntime 1.23, which has no 3.14 build; the engine
# venvs were tested on 3.13. Override with VIDX_PYTHON, VIDX_PYTHON_GIGA, VIDX_PYTHON_ENGINES.
PY_VERSION_DEFAULT, PY_GIGA_VERSION_DEFAULT, PY_ENGINES_VERSION_DEFAULT = "3.14", "3.13", "3.13"
