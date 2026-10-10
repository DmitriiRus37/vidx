"""pytest setup for vidx.

Run the unit tests (no models, no network, a few seconds):
    uv run --no-project --python 3.14 --with pytest --with numpy --with pillow pytest
Run the real-model tests too (macOS, models already downloaded by `vidx setup`, minutes):
    uv run --no-project --python 3.14 --with pytest --with numpy --with pillow pytest -m real

lib/ holds loose scripts that import each other as top-level modules (`from common import ...`),
so it goes on sys.path here, the same way Python puts a script's own directory there.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
