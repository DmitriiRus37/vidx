"""pytest setup for vidx.

Run the unit tests (no models, no network, about a minute), with the pinned test dependencies of
pyproject.toml's "test" group:
    uv run --isolated --group test pytest
    uv run --isolated --group test --python 3.11 pytest     # the oldest supported Python
Run the real-model tests instead (macOS, models already downloaded by `vidx setup`, minutes):
    HF_HUB_OFFLINE=1 uv run --isolated --group test pytest -m real
--isolated keeps uv out of .venv: that is vidx's own helper venv (vosk, pillow, numpy), and a plain
`uv run --group test` would install into it, or rebuild it for another --python.

lib/ holds loose scripts that import each other as top-level modules (`from common import ...`),
so it goes on sys.path here, the same way Python puts a script's own directory there.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))


@pytest.fixture(scope="session")
def lid_backend():
    """(python, model) of vidx's language-ID backend, for the real tests: the model id is read from vidx
    (LID_MODEL), the interpreter is the one its lid_py uses (mlx-whisper's, a uv tool). Skips if missing."""
    assigns = re.findall(r"^(?:M_TURBO|LID_MODEL)=.*$", (ROOT / "vidx").read_text(), re.M)
    model = subprocess.run(["/bin/bash", "-c", "\n".join(assigns) + '\nprintf %s "$LID_MODEL"'],
                           capture_output=True, text=True, check=True).stdout
    uv = shutil.which("uv")
    d = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True).stdout.strip() if uv else ""
    python = Path(d) / "mlx-whisper" / "bin" / "python"
    if not model or not d or not python.exists():
        pytest.skip("language-ID backend not installed (vidx setup)")
    return python, model
