"""Pinned tools and environments that `vidx setup` installs."""

# Models and pinned dependencies. yt-dlp stays unpinned on purpose: YouTube breaks old versions.
TOOLS = ["mlx-whisper==0.4.3", "parakeet-mlx==0.5.3", "yt-dlp[default]"]
HELPER_PKGS = ["vosk==0.3.44", "pillow==12.3.0", "numpy==2.4.6"]
# Python for the venvs: the code runs on 3.11+, GigaAM pins onnxruntime 1.23 which has no 3.14 build.
PY_VERSION_DEFAULT, PY_GIGA_VERSION_DEFAULT = "3.14", "3.13"
GIGA_PKGS = ["gigaam[torch] @ git+https://github.com/salute-developers/GigaAM@7447938d791c4f3e643386ee22c33777004293a5",
             "torch==2.14.1", "torchaudio==2.11.0", "soundfile==0.14.0", "numpy==2.4.6"]


def uv_pip(venv):
    """`uv pip install` into a venv (VIRTUAL_ENV is not needed: --python names the interpreter's venv)."""
    return ["uv", "pip", "install", "-q", "--python", venv]
