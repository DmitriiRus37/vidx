"""pytest setup for vidx.

Run the unit tests (no models, no network, about a minute), with the pinned test dependencies of
pyproject.toml's "test" group:
    uv run --isolated --group test pytest
    uv run --isolated --group test --python 3.11 pytest     # the oldest supported Python
Run the real-model tests instead (macOS, models already downloaded by `vidx setup`, minutes):
    HF_HUB_OFFLINE=1 uv run --isolated --group test pytest -m real
--isolated keeps uv out of .venv: that is vidx's own helper venv (vosk, pillow, numpy), and a plain
`uv run --group test` would install into it, or rebuild it for another --python.

src/vidx/lib/ holds loose scripts that import each other as top-level modules (`from common import ...`;
they run in the engines' own interpreters), so it goes on sys.path here, the same way Python puts a
script's own directory there. The vidx package itself is installed into the test environment by uv.
"""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "vidx" / "lib"))


@pytest.fixture(scope="session")
def lid_backend():
    """(python, model) of vidx's language-ID backend on this platform, for the real tests: the model id and
    the interpreter come from the engine registry (vidx.engines). Skips if missing."""
    from vidx import paths
    from vidx.engines import Registry

    reg = Registry(paths.platform_of(), paths.home())
    if reg.platform == "darwin" and not shutil.which("uv"):
        pytest.skip("language-ID backend not installed (vidx setup)")
    python = Path(reg.lid_argv("lid")[0])
    if not python.exists():
        pytest.skip("language-ID backend not installed (vidx setup)")
    return python, reg.lid.model


@pytest.fixture(autouse=True)
def _not_interrupted():
    """vidx.jobs keeps "interrupted" process-wide (a run ends after it); a test that sets it must not stop
    the programs of the next test."""
    from vidx import jobs

    jobs._interrupted.clear()
    yield
    jobs._interrupted.clear()
